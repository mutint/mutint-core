from django.apps import AppConfig


class ImportConfig(AppConfig):
    name = "mutint_import"

    def ready(self):
        from mutint_common.import_tab_registry import register_import_tab
        from mutint_import.handlers import register_core_import_handlers
        register_core_import_handlers()

        # The tabs core ships, in the order the page shows them. Core is a caller of the tab
        # registry like any plugin; a plugin's tab lands after these, and may be a page of its
        # own (mutint-breseq's Run breseq). The labels are the page's words, not the
        # handlers' -- a handler's label names formats, a tab names a kind of import.
        #
        # Reference Sequence is two handlers behind one tab: `reference` until the experiment
        # has one, `replace_annotation` after. They are one question -- "what is this
        # experiment aligned to?" -- at two moments, and a Replace Annotation tab beside a
        # Reference Sequence tab that said "already has one" was two tabs for it.
        register_import_tab("reference", "Reference Sequence",
                            import_types=("reference", "replace_annotation"))
        register_import_tab("genomediff", "Genome Diff", import_type="genomediff")
        register_import_tab("vcf", "Variant Call Format", import_type="vcf")
        # "Results Folder" rather than "breseq output": the tab is for what a pipeline left
        # behind, and breseq's is the one kind it reads today.
        register_import_tab("breseq_folder", "Results Folder", import_type="breseq_folder")

    # No nav entry. Importing happens inside an experiment -- the button on the
    # experiment's own page (stats.html) is the way in -- so a sidebar link could only ever
    # land on /import/ with no experiment to import into.
