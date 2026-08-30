"""Session middleware that a view can ask not to save.

``SESSION_SAVE_EVERY_REQUEST`` is on, which writes a ``django_session`` row on every request
so that activity keeps a session alive. That is right for pages and wrong for one endpoint:
the Add page's progress poll.

**A poll must be a pure reader, or it waits for the import it is reporting on.** Under WAL a
reader is never blocked, so a poll that only reads answers instantly no matter how long the
importer's transaction runs. Add one write and it has to queue for the write lock -- which
``BEGIN IMMEDIATE`` holds for the whole of each sample -- so the poll takes as long as the
sample in flight. On a 20-30 sample GenomeDiff drop that showed as the progress bar advancing
once every few seconds, and as the table not appearing at all until the first sample had
committed: the poll that would have drawn it was itself stuck behind that sample.

The session had nothing to do with any of it. The poll reads `request.user` to check who owns
the upload, and that is all it wants from the session.

**Why a subclass rather than turning the setting off.** Off, sessions would stop being kept
alive by activity everywhere -- a real change to when people get logged out, for one
endpoint's benefit. And why a subclass rather than the usual trick of clearing
``request.session`` in a later middleware: that works only by tripping Django's
``except AttributeError`` guard, which is an implementation detail, and it depends on this
middleware's position in the list staying where it is. Overriding the one method says what is
meant.

A view opts in with::

    request.aledb_skip_session_save = True

Nothing else changes: an unflagged request is saved exactly as before.
"""

from django.contrib.sessions.middleware import SessionMiddleware


class SkipSaveSessionMiddleware(SessionMiddleware):
    """`SessionMiddleware`, minus the save on requests that asked to be left alone."""

    def process_response(self, request, response):
        if getattr(request, "aledb_skip_session_save", False):
            # No save, and no cookie or Vary handling either -- all of which exist to keep a
            # browsing session alive, and none of which a JSON poll wants.
            return response
        return super().process_response(request, response)
