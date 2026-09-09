import logging
import os
import re
import subprocess



logger = logging.getLogger(__name__)

# This file is mutint_common/util.py, so its grandparent is the mutint-core checkout.
MUTINT_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Keyed by absolute directory. The code under a running process does not change, and the
# About page would otherwise shell out twice per component on every request.
_revisions = {}

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


def _github_repository_url(remote):
    """The github.com page of the repository `remote` names, or None when it is not on GitHub.

    Read off the remote rather than assumed: a component's home is wherever its checkout was
    cloned from, under whichever owner, and one published elsewhere gets no link rather than a
    wrong one. Both the https:// and the git@ forms count.
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
    return 'https://github.com/%s' % path if path else None


def _github_commit_url(remote, sha):
    """A github.com commit URL for `remote`, or None when it is not a GitHub remote.

    A hash is only worth clicking if it resolves somewhere. A checkout whose remote is a
    relative local path (`../mutint-core`) renders its revision as plain text; one cloned
    from GitHub gets a link.
    """
    repository = _github_repository_url(remote)
    return repository + '/commit/' + sha if repository else None


def get_revision(directory):
    """``{'short', 'full', 'committed', 'url', 'repository'}`` for the repository at
    `directory`, or None.

    `url` is the commit's page and `repository` the repository's own, both None unless the
    origin remote is on GitHub. `committed` is the commit's own timestamp in strict ISO 8601,
    which is what lets a reader see *how old* what they are running is rather than only which
    hash it is -- a SHA says nothing about age, and on the Development channel that is the
    question being asked. None wherever the revision itself is unknown.

    The result is cached per directory for the life of the process, so the third git command
    costs nothing beyond the first render.
    """
    directory = os.path.abspath(directory)
    if directory not in _revisions:
        full = _git(directory, 'rev-parse', 'HEAD')
        remote = _git(directory, 'remote', 'get-url', 'origin') if full else None
        committed = _git(directory, 'show', '-s', '--format=%cI', 'HEAD') if full else None
        _revisions[directory] = None if full is None else {
            'short': full[:7],
            'full': full,
            'committed': committed,
            'url': _github_commit_url(remote, full),
            'repository': _github_repository_url(remote),
        }
    return _revisions[directory]


_asset_version = None


def compute_asset_version():
    """The `?v=` every first-party asset is linked with: the version, plus a digest of every
    installed component's git revision when any is known.

    A release changes the version and so every URL; that used to be the whole rule, and it
    stopped being enough the moment the Development channel existed -- an upgrade to the
    next `main` commit keeps the version, so a browser kept the previous commit's script
    under the same URL and rendered new rows with old code. Every component's HEAD is in the
    digest, in INSTALLED_APPS order, so a plugin's commit changes the URLs too. Uncached,
    so a test can drive it with `get_revision` patched; `get_asset_version` is the cached
    form a request reads.

    `__version__` alone where no revision is known -- an unpacked archive with no `.git` --
    which is the rule a release relied on before, and still holds there.
    """
    import hashlib

    from mutint_common.about_registry import component_dir, first_party_app_configs
    from mutint_common.version import __version__

    shas, seen = [], set()
    for app_config in first_party_app_configs():
        directory = component_dir(app_config)
        if directory in seen:
            continue
        seen.add(directory)
        revision = get_revision(directory)
        if revision:
            shas.append(revision['full'])
    if not shas:
        return __version__
    return '%s+%s' % (__version__, hashlib.sha256('\n'.join(shas).encode()).hexdigest()[:8])


def get_asset_version():
    """`compute_asset_version`, once per process: the code under a running process does not
    change, and it is asked for on every request. Lazy rather than at import, because the app
    registry it walks is not ready then."""
    global _asset_version
    if _asset_version is None:
        _asset_version = compute_asset_version()
    return _asset_version


def get_git_hash():
    """mutint-core's own revision in full, as a string; empty outside a repository.

    Two things it used to do are worth not doing again. It ran git in the *process working
    directory*, which under an assembled project is wherever the server was started and not
    this repository at all; it now asks about a directory it derives from `__file__`. And it
    returned `check_output`'s bytes, so `{{ git_hash }}` rendered `b'56be47ad...'`, prefix
    and quotes included, on every page that shows it.
    """
    revision = get_revision(MUTINT_CORE_DIR)
    return revision['full'] if revision else ''


# Bound unconditionally, and by a call that cannot raise. This was a module-level `try` that
# assigned only on success, so a checkout without .git left the name undefined and every
# request died in get_user_context() with a NameError -- losing the whole site rather than
# one line of it.
common_context = {"git_hash": get_git_hash()}


def get_user_context(user):
    context = common_context.copy()
    return context


# Gene annotation parsing — used across mutint_sample, mutint_filter, mutint_converge, mutint_stats
#
# `Mutation.gene` is written with this delimiter, and is *also* read back holding the other
# spelling -- see GENE_LIST_SPLIT below for why both exist and why this one cannot simply be
# corrected.
GENE_RANGE_ANNOTATION_DELIMITER = ", "

# What splits a stored gene string, and it is deliberately tolerant of the space.
#
# A mutation spanning a gene range stores every name breseq listed, and those arrive through
# `annotate.annotator`, which joins them with a bare comma (`GENE_LIST_SEPARATOR = ','`). Every
# other shape -- intergenic, single gene -- is joined by the import path with ", ". So the
# column holds both spellings, and splitting on ", " alone read a 4,318-gene inversion as one
# gene name 23,003 characters long: no expander in the Gene column, no ecocyc links, and a
# reader's ignored-gene list that could never match any of the names in it.
#
# **The writer is not the thing to fix.** `gene` is one of the seven fields
# `Mutation.objects.get_or_create` keys on, so re-joining that list with ", " would change the
# stored string for exactly these mutations and fork every one of them on the next import. The
# stored data is inconsistent, and a reader that accepts both spellings is the honest response
# to that -- not a rewrite of what 41,671 rows already say.
GENE_LIST_SPLIT = re.compile(r",\s*")

# Past this many genes the names are neither recorded nor rendered.
#
# A structural variant can span most of a chromosome -- the widest in the dev database is a
# 4,318-gene inversion -- and the names are then 23,003 characters of `Mutation.gene`, which is
# a `CharField(max_length=19000)`: over its own limit already, silently, because SQLite does not
# enforce one and Postgres would have refused the row. Nothing reads a list that long. It is not
# a gene annotation any more, it is the chromosome.
#
# So `get_annotated_gene_list` records the *range* instead (`mokC–[fimA]`, breseq's own Gene
# column for such a mutation) and the tables render that, with no expander to open. The limit
# lives here rather than in either caller because the importer and the renderer have to agree:
# a row written under one limit and read under another would show a truncation nothing did.
GENE_LIST_LIMIT = 1000

INTRAGENIC_LEFT_CHAR = ']'
INTRAGENIC_RIGHT_CHAR = '['


def get_gene_list(annotated_gene_list_str):
    """Parse a breseq gene annotation string into a clean list of gene names.

    Split with `GENE_LIST_SPLIT`, which accepts a comma with or without the space after it,
    because the column holds both spellings -- see the constant for which writer produces
    which, and why the writer is the wrong end to correct.
    """
    annotated_gene_list = GENE_LIST_SPLIT.split(annotated_gene_list_str)
    clean_gene_list = []
    for gene in annotated_gene_list:
        gene = gene.replace(INTRAGENIC_LEFT_CHAR, '').replace(INTRAGENIC_RIGHT_CHAR, '')
        clean_gene_list.append(gene)
    return clean_gene_list
