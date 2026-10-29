"""Read-only database/Redis readiness and least-privilege assertion."""
import os
import django


def main():
    django.setup()
    from django.db import connection
    from redis import Redis
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), current_user, rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname=current_user")
        row = cursor.fetchone()
        if row != ("onepost_fresh", "onepost_app", False, False, False):
            raise SystemExit("Unexpected database identity or elevated role")
        cursor.execute("SELECT 1")
        assert cursor.fetchone() == (1,)
    assert Redis.from_url(os.environ["REDIS_URL"], socket_timeout=3).ping()
    print("PASS: dedicated PostgreSQL identity, limited role, SQL and Redis connectivity")


if __name__ == "__main__":
    main()
