from alembic import context
from sqlalchemy import engine_from_config, pool

from backend.database import DATABASE_URL
from backend.models import Base


config = context.config
database_url = (DATABASE_URL.render_as_string(hide_password=False)
                if hasattr(DATABASE_URL, "render_as_string") else DATABASE_URL)
# ConfigParser treats percent signs as interpolation markers. URL-encoded
# credentials can legitimately contain them (for example, "%25" for "%"),
# so escape them only for Alembic's configuration layer.
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(url=database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.", poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_offline() if context.is_offline_mode() else run_migrations_online()
