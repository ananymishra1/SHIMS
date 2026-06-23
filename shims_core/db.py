from __future__ import annotations

from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from .settings import resolve_sqlite_url, settings
from .models import Base

enterprise_engine = create_engine(resolve_sqlite_url(settings.enterprise_database_url), connect_args={'check_same_thread': False}, future=True)
omni_engine = create_engine(resolve_sqlite_url(settings.omni_database_url), connect_args={'check_same_thread': False}, future=True)

EnterpriseSession = sessionmaker(bind=enterprise_engine, autoflush=False, autocommit=False, future=True)
OmniSession = sessionmaker(bind=omni_engine, autoflush=False, autocommit=False, future=True)


def init_enterprise_db() -> None:
    Base.metadata.create_all(enterprise_engine)


def init_omni_db() -> None:
    Base.metadata.create_all(omni_engine)


def enterprise_db():
    db = EnterpriseSession()
    try:
        yield db
    finally:
        db.close()


def omni_db():
    db = OmniSession()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def enterprise_session():
    db = EnterpriseSession()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
