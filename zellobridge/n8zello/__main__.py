"""
Start:          python -m n8zello
Superadmin:     python -m n8zello set-superadmin USERNAME     (asks for the password)
New password:   python -m n8zello reset-password USERNAME
Settings:       python -m n8zello show-settings

Passwords can also come from the environment variable N8ZELLO_PASSWORD (scripts).
"""
import getpass
import json
import os
import sys

G, N = "\033[0;32m", "\033[0m"


def _password(prompt: str) -> str:
    pw = os.environ.get("N8ZELLO_PASSWORD")
    if pw is None:
        pw = getpass.getpass(prompt)
        if getpass.getpass("Repeat: ") != pw:
            raise SystemExit("The passwords differ.")
    if len(pw) < 10:
        raise SystemExit("At least 10 characters.")
    return pw


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] == "run":
        from .app import main as run
        run()
        return 0
    from .auth import Auth, AuthError
    from .db import DB
    from .app import DATA
    from .settings import Settings
    db = DB(DATA / "n8zello.db")
    try:
        if args[0] == "set-superadmin" and len(args) > 1:
            Auth(db).set_superadmin(args[1], _password(f"Password for {args[1]} (min. 10): "))
            print(f"{G}✓{N} {args[1]} is the superadmin (password stored as scrypt hash).")
            return 0
        if args[0] == "reset-password" and len(args) > 1:
            Auth(db).reset(args[1], _password(f"New password for {args[1]} (min. 10): "))
            print(f"{G}✓{N} Password of {args[1]} set.")
            return 0
        if args[0] == "show-settings":
            s = Settings(db).all()
            s["_superadmin"] = Auth(db).has_superadmin()
            print(json.dumps(s, ensure_ascii=False, indent=1))
            return 0
        if args[0] == "set" and len(args) > 2:
            Settings(db).update({args[1]: args[2]}, "cli")
            print(f"{G}✓{N} {args[1]} = {args[2]}")
            return 0
    except AuthError as e:
        raise SystemExit(str(e))
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
