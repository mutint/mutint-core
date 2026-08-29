
__author__ = 'Patrick Phaneuf'

# `AleName` and `parse_ale_name` lived here and are gone. They read a coordinate out of a
# filename with a bare `except: return 1`, so every field they could not read became 1 and
# every non-conforming name collapsed onto the same sample. `aledb_import.sample_names` is
# the reading of a filename now, and it answers None rather than guessing.


def sanitize_path(path):
    if path[-1] != '/':
        path += '/'
    return path


def get_ale_isolate_name_from_path(breseq_report_path):
    ale_isolate_name_start_index = breseq_report_path.rfind('/')
    if ale_isolate_name_start_index >= len(breseq_report_path) - 1:
        ale_isolate_name_start_index = breseq_report_path[:-2].rfind('/')
    ale_isolate_name = breseq_report_path[ale_isolate_name_start_index:]
    ale_isolate_name = ale_isolate_name.strip('/')
    return ale_isolate_name
