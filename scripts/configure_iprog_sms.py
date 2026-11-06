"""Save the IPROG token privately for the current Windows user; sends no SMS."""
import getpass
from pathlib import Path
import sys
import warnings
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.sms_config import save_token
from core.sms_sender import check_token


if __name__ == '__main__':
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',getpass.GetPassWarning)
            token = getpass.getpass('IPROG API token (hidden): ')
        valid, message = check_token(token)
        if not valid:
            print(message+' The existing saved token was not replaced.',file=sys.stderr)
            sys.exit(1)
        save_token(token)
        print('Encrypted IPROG token saved for this Windows user. Restart CBVMS. No SMS was sent.')
    except (Exception,KeyboardInterrupt):
        print('SMS setup not completed. Run in an interactive PowerShell and enter a valid replacement token.',file=sys.stderr)
        sys.exit(1)
