"""Windows-user-bound IPROG secret storage using DPAPI, with environment override."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path

TOKEN_PATH = Path(__file__).resolve().parents[1] / 'data' / 'iprog-token.dpapi'


def _crypt(data, *, protect):
    if os.name != 'nt':
        raise RuntimeError('Encrypted SMS setup requires Windows. Use IPROG_API_TOKEN on other systems.')
    class Blob(ctypes.Structure):
        _fields_ = [('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt = ctypes.WinDLL('crypt32',use_last_error=True)
    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if protect:
        operation = crypt.CryptProtectData
        operation.argtypes = [ctypes.POINTER(Blob),wintypes.LPCWSTR,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
        ok = operation(ctypes.byref(source),'CBVMS IPROG SMS',None,None,None,1,ctypes.byref(output))
    else:
        operation = crypt.CryptUnprotectData
        operation.argtypes = [ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
        ok = operation(ctypes.byref(source),None,None,None,None,1,ctypes.byref(output))
    if not ok:
        raise RuntimeError('SMS secret could not be accessed by this Windows user.')
    try:
        return ctypes.string_at(output.data,output.size)
    finally:
        kernel.LocalFree(output.data)


def save_token(token):
    token = token.strip()
    if not 16 <= len(token) <= 256 or any(ord(c)<33 or ord(c)>126 for c in token):
        raise ValueError('Enter a valid IPROG API token.')
    encrypted = _crypt(token.encode('utf-8'),protect=True)
    TOKEN_PATH.parent.mkdir(parents=True,exist_ok=True)
    temporary = TOKEN_PATH.with_suffix('.tmp')
    temporary.write_bytes(encrypted)
    temporary.replace(TOKEN_PATH)


def get_token():
    token = os.environ.get('IPROG_API_TOKEN','').strip()
    if token:
        return token
    try:
        if TOKEN_PATH.stat().st_size > 65536:
            return ''
        return _crypt(TOKEN_PATH.read_bytes(),protect=False).decode('utf-8').strip()
    except (OSError,RuntimeError,ValueError):
        return ''
