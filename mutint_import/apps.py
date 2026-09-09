from django.apps import AppConfig


class ImportConfig(AppConfig):
    name = "mutint_import"

    def ready(self):
        from mutint_common.import_tab_registry import register_import_tab
        from mutint_common.nav_registry import EXPERIMENT_SECTION, register_nav_item
        from mutint_import.handlers import register_core_import_handlers
        register_core_import_handlers()

        # `requires_edit`: the page refuses a reader with no write role, so an entry
        # leading them to it would be a dead end -- which is the objection this
        # codebase raises against every nav entry that 403s.
        register_nav_item('Import Data', url='/import/', section=EXPERIMENT_SECTION,
                          requires_edit=True)

        # The tabs core ships, in the order the page shows them. Core is a caller of the tab
        # registry like any plugin; a plugin's tab lands after these, and may be a page of its
        # own (mutint-breseq's Run breseq). The labels are the page's words, not the
        # handlers' -- a handler's label names formats, a tab names a kind of import.
        #
        # **Two tabs for one question at two moments, and only ever one of them showing.**
        # This was a single Reference Sequence tab naming both handlers, which is what kept a
        # Replace Annotation tab from sitting beside a Reference Sequence tab that said
        # "already has one" -- the two are never offered together, so a tab per moment says
        # the same thing without that risk, and lets each be named and placed for its moment.
        #
        # Establishing a reference is the first thing an experiment needs, so that tab leads.
        register_import_tab("reference", "Reference Sequence", import_type="reference")
        register_import_tab("genomediff", "Genome Diff", import_type="genomediff")
        register_import_tab("vcf", "Variant Call Format", import_type="vcf")
        # "Results Folder" rather than "breseq output": the tab is for what a pipeline left
        # behind, and breseq's is the one kind it reads today.
        register_import_tab("breseq_folder", "Results Folder", import_type="breseq_folder")
        # Last, after the plugins' tabs: correcting the annotation of an experiment that is
        # already set up is not a way of getting data into one, and it is the tab somebody
        # wants least often. `reference` above is the same question at the other moment and
        # they are never offered together.
        register_import_tab("update_annotation", "Update Annotation",
                            import_type="replace_annotation", last=True)

    # The nav entry is EXPERIMENT_SECTION and could not have been anywhere else. It used to
    # have none at all, on the reasoning that "a sidebar link could only ever land on
    # /import/ with no experiment to import into" -- true of MAIN_SECTION, which is the only
    # section that existed for it at the time. An experiment-section entry renders only when
    # one is selected and carries `?experiment_id=`, which is exactly the objection answered.
