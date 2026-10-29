"""Explicit PostgreSQL opt-in; SQLite remains the default."""
def database_config(env, base_dir):
    backend = env.get("DATABASE_BACKEND", "sqlite")
    if backend == "sqlite":
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": base_dir / "db.sqlite3"}
    if backend != "postgresql":
        raise ValueError("Unsupported DATABASE_BACKEND")
    required = ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_HOST")
    if any(not env.get(key) for key in required):
        raise ValueError("PostgreSQL opt-in requires database, user, password and host")
    return {
        "ENGINE": "django.db.backends.postgresql", "NAME": env["POSTGRES_DB"],
        "USER": env["POSTGRES_USER"], "PASSWORD": env["POSTGRES_PASSWORD"],
        "HOST": env["POSTGRES_HOST"], "PORT": env.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 0, "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {"connect_timeout": 3, "sslmode": env.get("POSTGRES_SSLMODE", "prefer")},
    }
