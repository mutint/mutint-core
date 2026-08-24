import logging
import os
import subprocess

from aledb_experiment.models import TechnicalReplicate
from django.core.cache import cache

__author__ = 'Patrick Phaneuf, Denny Gosting'

logger = logging.getLogger(__name__)

# This file is aledb_common/util.py, so its grandparent is the aledb-core checkout.
ALEDB_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Keyed by absolute directory. The code under a running process does not change, and the
# About page would otherwise shell out twice per component on every request.
_revisions = {}

def clear_dashboard_cache():

    cache.delete('dashboard_mutation')

    cache.delete('dashboard_observed_mutation')

    cache.delete('dashboard_bar_chart_gene_dict')


def _git(directory, *args):
    """One git command run in `directory`, or None if it could not be run.

    Every failure is the same answer -- there is no revision to show. A machine without git,
    a deployment shipped without its .git (production images usually are), a directory that
    is not a repository, a command that hangs: none of them deserve an exception, because
    all that hangs off this is a line on the About page.
    """
    try:
        finished = subprocess.run(['git', '-C', directory] + list(args),
                                  capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if finished.returncode != 0:
        return None
    return finished.stdout.decode('utf-8', 'replace').strip() or None


def _github_commit_url(remote, sha):
    """A github.com commit URL for `remote`, or None when it is not a GitHub remote.

    A hash is only worth clicking if it resolves somewhere. This suite's submodules point at
    relative local paths (`../aledb-core`), so their revisions render as plain text; a
    deployment cloned from GitHub gets links. Both the https:// and the git@ forms count.
    """
    if not remote:
        return None
    if remote.startswith('git@github.com:'):
        path = remote[len('git@github.com:'):]
    elif remote.startswith(('https://github.com/', 'http://github.com/')):
        path = remote.split('github.com/', 1)[1]
    else:
        return None
    if path.endswith('.git'):
        path = path[:-len('.git')]
    path = path.strip('/')
    return 'https://github.com/%s/commit/%s' % (path, sha) if path else None


def get_revision(directory):
    """``{'short', 'full', 'url'}`` for the repository at `directory`, or None.

    `url` is None unless the origin remote is on GitHub. The result is cached per directory
    for the life of the process.
    """
    directory = os.path.abspath(directory)
    if directory not in _revisions:
        full = _git(directory, 'rev-parse', 'HEAD')
        _revisions[directory] = None if full is None else {
            'short': full[:7],
            'full': full,
            'url': _github_commit_url(_git(directory, 'remote', 'get-url', 'origin'), full),
        }
    return _revisions[directory]


def get_git_hash():
    """aledb-core's own revision in full, as a string; empty outside a repository.

    Two things it used to do are worth not doing again. It ran git in the *process working
    directory*, which under an assembled project is wherever the server was started and not
    this repository at all; it now asks about a directory it derives from `__file__`. And it
    returned `check_output`'s bytes, so `{{ git_hash }}` rendered `b'56be47ad...'`, prefix
    and quotes included, on every page that shows it.
    """
    revision = get_revision(ALEDB_CORE_DIR)
    return revision['full'] if revision else ''


# Bound unconditionally, and by a call that cannot raise. This was a module-level `try` that
# assigned only on success, so a checkout without .git left the name undefined and every
# request died in get_user_context() with a NameError -- losing the whole site rather than
# one line of it.
common_context = {"git_hash": get_git_hash()}


def get_user_context(user):
    context = common_context.copy()
    # context.update({"experiments": get_all_user_exps(user)})
    return context


def is_int(s):
    try:
        int(s)
        return True
    except ValueError:
        return False


# Gene annotation parsing — used across aledb_seq, aledb_filter, aledb_converge, aledb_stats
GENE_RANGE_ANNOTATION_DELIMITER = ", "
INTRAGENIC_LEFT_CHAR = ']'
INTRAGENIC_RIGHT_CHAR = '['


def _find_between(s, first, last):
    try:
        start = s.index(first) + len(first)
        end = s.index(last, start)
        return s[start:end]
    except ValueError:
        return ""


def get_gene_list(annotated_gene_list_str):
    """Parse a breseq gene annotation string into a clean list of gene names."""
    annotated_gene_list = annotated_gene_list_str.split(GENE_RANGE_ANNOTATION_DELIMITER)
    clean_gene_list = []
    for gene in annotated_gene_list:
        gene = gene.replace(INTRAGENIC_LEFT_CHAR, '').replace(INTRAGENIC_RIGHT_CHAR, '')
        clean_gene_list.append(gene)
    return clean_gene_list
