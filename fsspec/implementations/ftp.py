from ftplib import FTP
import uuid
from ..spec import AbstractBufferedFile, AbstractFileSystem
from ..utils import infer_storage_options


class FTPFileSystem(AbstractFileSystem):
    """A filesystem over classic """

    def __init__(self, host, port=21, username=None, password=None,
                 acct=None, block_size=None, tempdir='/tmp', **kwargs):
        """
        You can use _get_kwargs_from_urls to get some kwargs from
        a reasonable FTP url.

        Authentication will be anonymous if username/password are not
        given.

        Parameters
        ----------
        host: str
            The remote server name/ip to connect to
        port: int
            Port to connect with
        username: str or None
            If authenticating, the user's identifier
        password: str of None
            User's password on the server, if using
        acct: str or None
            Some servers also need an "account" string for auth
        block_size: int or None
            If given, the read-ahead or write buffer size.
        tempdir: str
            Directory on remote to put temporary files when in a transaction
        """
        super(FTPFileSystem, self).__init__()
        self.ftp = FTP()
        self.host = host
        self.port = port
        self.tempdir = tempdir
        self.dircache = {}
        if block_size is not None:
            self.blocksize = block_size
        self.ftp.connect(host, port)
        self.ftp.login(username, password, acct)

    @classmethod
    def _strip_protocol(cls, path):
        return infer_storage_options(path)['path']

    @staticmethod
    def _get_kwargs_from_urls(urlpath):
        return infer_storage_options(urlpath)

    def invalidate_cache(self, path=None):
        if path is not None:
            self.dircache.pop(path, None)
        else:
            self.dircache.clear()

    def ls(self, path, detail=True):
        path = path.rstrip('/')
        if path not in self.dircache:
            self.dircache[path] = list(self.ftp.mlsd(path))
        files = self.dircache[path]
        if not detail:
            return sorted(['/'.join([path, f[0]]) for f in files])
        out = []
        for fn, details in sorted(files):
            if fn in ['.', '..']:
                continue
            details['name'] = '/'.join([path, fn])
            if details['type'] == 'file':
                details['size'] = int(details['size'])
            else:
                details['size'] = 0
            out.append(details)
        return out

    def info(self, path):
        # implement with direct method
        parent = path.rsplit('/', 1)[0]
        files = self.ls(parent, True)
        return [f for f in files if f['name'] == path][0]

    def _open(self, path, mode='rb', block_size=None, autocommit=True,
              **kwargs):
        block_size = block_size or self.blocksize
        return FTPFile(self, path, mode=mode, block_size=block_size,
                       tempdir=self.tempdir, autocommit=autocommit)

    def _rm(self, path):
        self.ftp.delete(path)
        self.invalidate_cache(path.rsplit('/', 1)[0])

    def mkdir(self, path, **kwargs):
        self.ftp.mkd(path)

    def rmdir(self, path):
        self.ftp.rmd(path)

    def mv(self, path1, path2, **kwargs):
        self.ftp.rename(path1, path2)
        self.invalidate_cache(path1.rsplit('/', 1)[0])
        self.invalidate_cache(path2.rsplit('/', 1)[0])


class TransferDone(Exception):
    """Internal exception to break out of transfer"""
    pass


class FTPFile(AbstractBufferedFile):
    """Interact with a remote FTP file with read/write buffering"""

    def __init__(self, fs, path, **kwargs):
        super().__init__(fs, path, **kwargs)
        if kwargs.get('autocommit', False) is False:
            self.target = self.path
            self.path = '/'.join([kwargs['tempdir'], str(uuid.uuid4())])

    def commit(self):
        self.fs.mv(self.path, self.target)

    def discard(self):
        self.fs.rm(self.path)

    def _fetch_range(self, start, end):
        """Get bytes between given byte limits

        Implemented by raising an exception in the fetch callback when the
        number of bytes received reaches the requested amount.

        With fail if the server does not respect the REST command on
        retrieve requests.
        """
        out = []
        total = [0]

        def callback(x):
            total[0] += len(x)
            if total[0] > end - start:
                out.append(x[:(end - start) - total[0]])
                self.fs.ftp.abort()
                raise TransferDone
            else:
                out.append(x)

            if total[0] == end - start:
                raise TransferDone

        try:
            self.fs.ftp.retrbinary('RETR %s' % self.path, blocksize=2**16,
                                   rest=start, callback=callback)
        except TransferDone:
            pass
        return b''.join(out)

    def _upload_chunk(self, final=False):
        self.buffer.seek(0)
        self.fs.ftp.storbinary("STOR " + self.path, self.buffer,
                               blocksize=2**16, rest=self.offset)
        return True
