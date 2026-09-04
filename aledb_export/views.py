from django.http import HttpResponse
from zipstream import ZipStream, ZIP_DEFLATED
from django.shortcuts import get_object_or_404
from aledb_experiment.utils import get_all_user_exps
from aledb_experiment.permissions import can_view_project
from aledb_experiment.models import Project, live
from zipfile import ZipFile
import io, csv
from aledb_export.util import get_csv_str
from aledb_filter.view_filter import get_view_filter
from aledb_common.logger import user_extra
import logging
import re
from tempfile import NamedTemporaryFile
from django.http import FileResponse
import os
import zipfile

logger = logging.getLogger(__name__)

def safe_filename(name):
    return re.sub(r'[^a-zA-Z0-9_\-]', '_', name)

def export(request):
    logger.info("export", extra = user_extra(request))
    try:
        exp_id_str = request.GET.get('experiment_ids', None)
        mut_type_str = request.GET.get('mut_type', None)
        project_id = request.GET.get('project_id', None)

        if project_id != 'null':
            project = get_object_or_404(Project, pk=int(project_id))
            if project and can_view_project(request.user, project):
                experiments = live(project.aleexperiment_set.all())
            else:
                return HttpResponse(status=403)
        else:
            experiments = get_all_user_exps(request.user)

        if mut_type_str and exp_id_str:
            exp_id_set = set(exp_id_str.split(','))
            # `exp.id`, not `exp.ale_id`. `AleExperiment`'s primary key was called
            # `ale_id` -- the same word as `AleId.ale_id`, which is a lineage label on
            # a different table -- and was renamed to the implicit `id` like every
            # other table's. These two lines were missed, so both export paths have
            # been raising AttributeError on every request since. `paths.EXPERIMENT_PK`
            # exists so that lookup *strings* survive that rename; an attribute access
            # is what it cannot reach.
            exp_list = [exp for exp in experiments if str(exp.id) in exp_id_set]
            if len(exp_list) > 0:
                tmp_file = NamedTemporaryFile(delete=False, suffix=".zip")
                try:
                    with zipfile.ZipFile(tmp_file, 'w', zipfile.ZIP_DEFLATED) as zf:
                        for experiment in exp_list:
                            csv_buffer = io.StringIO()
                            writer = csv.writer(csv_buffer)
                            # Per experiment, not once: a multi-experiment zip carries each
                            # experiment's own filter, so every file is what that
                            # experiment's page was showing.
                            writer.writerows(get_csv_str(
                                experiment.id, mut_type_str,
                                get_view_filter(request, experiment.id)))
                            filename = f"Proj_{safe_filename(experiment.project.name)}_Exp_{safe_filename(experiment.name)}_ExpID{experiment.id}_{mut_type_str}.csv"
                            zf.writestr(filename, csv_buffer.getvalue())
                            logger.info(f"Added {filename} to zip", extra=user_extra(request))

                    tmp_file.seek(0)
                    response = FileResponse(open(tmp_file.name, 'rb'), content_type='application/zip')
                    response['Content-Disposition'] = 'attachment; filename="download.zip"'

                    # Schedule cleanup
                    def cleanup(file_path):
                        try:
                            os.remove(file_path)
                        except Exception as e:
                            logger.warning(f"Could not delete temp file {file_path}: {e}")

                    response.close = lambda close=response.close: (close(), cleanup(tmp_file.name))
                    return response

                except Exception:
                    logger.exception("export broke", extra=user_extra(request))
                    return HttpResponse("Internal server error during export.", status=500)
    except Exception :
        logger.exception("export broke", extra = user_extra(request))
        return HttpResponse("Internal server error during export.", status=500)
    # Catch any path that doesn't return explicitly
    return HttpResponse("Invalid export request.", status=400)


def export_experiment_index(request):
    logger.info("export_experiment_index", extra=user_extra(request))
    try:
        exp_id_str = request.GET.get('experiment_ids', None)
        project_id = request.GET.get('project_id', None)

        if project_id != 'null':
            project = get_object_or_404(Project, pk=int(project_id))
            if project and can_view_project(request.user, project):
                experiments = live(project.aleexperiment_set.all())
            else:
                return HttpResponse(status=403)
        else:
            experiments = get_all_user_exps(request.user)

        if exp_id_str:
            exp_id_set = set(exp_id_str.split(','))
            exp_list = [exp for exp in experiments if str(exp.id) in exp_id_set]
            if len(exp_list) > 0:
                response = HttpResponse(content_type='text/csv')
                response['Content-Disposition'] = 'attachment; filename="experiment_index.csv"'
                writer = csv.writer(response)
                writer.writerow(['ale_id', 'experiment_name', 'project_id', 'project_name', 'person', 'is_public', 'date'])
                for e in sorted(exp_list, key=lambda x: x.id):
                    writer.writerow([e.id, e.name, e.project_id, e.project.name, e.person, e.project.is_public, e.date_str()])
                return response
        return HttpResponse("Invalid export request.", status=400)
    except Exception:
        logger.exception("export_experiment_index broke", extra=user_extra(request))
        return HttpResponse("Internal server error during export.", status=500)
