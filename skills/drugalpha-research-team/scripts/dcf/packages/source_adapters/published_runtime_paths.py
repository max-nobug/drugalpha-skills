"""Resolve published pipeline reads to the configured runtime database."""
import os
from pathlib import Path


def runtime_data_root(key, fallback):
    configured = os.environ.get(key)
    if os.environ.get('NODE_ENV') == 'production':
        shared = Path(os.environ.get('INNOVEST_SHARED_DATA_ROOT') or '/srv/innovest/shared/data').resolve()
        if not configured or not Path(configured).is_absolute() or not Path(configured).resolve().is_relative_to(shared):
            raise ValueError('Production data root must use ' + key + ' inside shared data')
    return Path(configured) if configured else Path(fallback)


def pipeline_database_path(root):
    return runtime_data_root('INNOVEST_PIPELINE_RUNTIME_ROOT', Path(root) / 'pipeline_tracking/data') / 'pipeline_tracking.db'
