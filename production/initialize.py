"""One-off release preparation, never performed by individual worker processes."""
import django
from django.core.management import call_command


def main():
    django.setup()
    call_command("migrate", interactive=False)
    call_command("collectstatic", interactive=False, clear=True)


if __name__ == "__main__":
    main()
