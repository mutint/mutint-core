from django.apps import AppConfig


class ImportConfig(AppConfig):
    name = "mutint_import"

    def ready(self):
        from mutint_common.import_tab_registry import register_import_tab
        from mutint_import.handlers import register_core_import_handlers
        register_core_import_handlers()

        # One tab per type core ships, in the order the page shows them. Core is a caller of
        # the tab registry like any plugin; a plugin's tab lands after these, and may be a
        # page of its own (mutint-breseq's Run breseq). The labels are the page's words, not
        # the handlers' -- a handler's label names formats, a tab names a kind of import.
        register_import_tab("reference", "Reference Sequence", import_type="reference")
        register_import_tab("genomediff", "Genome Diff", import_type="genomediff")
        register_import_tab("vcf", "Variant Call Format", import_type="vcf")
        register_import_tab("breseq_folder", "breseq output", import_type="breseq_folder")
        register_import_tab("replace_annotation", "Replace Annotation",
                            import_type="replace_annotation")

    # No nav entry. Importing happens inside an experiment -- the button on the
    # experiment's own page (stats.html) is the way in -- so a sidebar link could only ever
    # land on /import/ with no experiment to import into.
