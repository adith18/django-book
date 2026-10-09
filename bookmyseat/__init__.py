# bookmyseat/__init__.py  (project package, next to settings.py)
from .celery import app as celery_app

__all__ = ('celery_app',)