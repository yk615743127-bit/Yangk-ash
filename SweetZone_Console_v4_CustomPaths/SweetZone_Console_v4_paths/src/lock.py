"""操作系统文件锁；进程退出后自动释放，支持Windows与Linux。"""
import os


class Timeout(RuntimeError):
    pass


class FileLock:
    def __init__(self, path, timeout=0):
        self.path = path
        self.file = None

    def __enter__(self):
        self.file = open(self.path,'a+b')
        self.file.seek(0,2)
        if self.file.tell()==0:
            self.file.write(b'0')
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise Timeout('已有任务持有文件锁') from None
        return self

    def __exit__(self,*args):
        try:
            self.file.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_UN)
        finally:
            self.file.close()
