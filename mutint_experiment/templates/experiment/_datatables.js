
    $(document).ready(function () {
        // The table scrolls in a box DataTables draws round it (the `t` in dom), not
        // through scrollY: see .mutint-scroll-table in common.css for why.
        var table = $('#exp_table').DataTable({
            autoWidth: true,
            paging: false,
            dom: 'Bfr<"mutint-scroll-table"t><<"pull-left"i><"pull-right"p>>',
            deferRender: true,
            columnDefs: [
                {
                    targets: 0,
                    orderable: false,
                    className: 'select-checkbox',
                }
            ],
            select: {
                style: 'multi',
                selector: 'td:first-child'
            },
            order: [[1, 'asc']],
            buttons: [
                {
                    extend: 'collection',
                    text: 'Export',
                    buttons: [
                        {
                            text: 'Mutations',
                            action: function () {
                                export_data(table, 'mut');
                            }
                        },
                        {% for type_str, label in plugin_export_types %}
                        {
                            text: '{{ label|escapejs }}',
                            action: function () {
                                export_data(table, '{{ type_str|escapejs }}');
                            }
                        },
                        {% endfor %}
                        {
                            text: 'Experiment List',
                            action: function () {
                                export_experiment_index(table)
                            }
                        },
                    ]
                },
            ],
        });
        window.mutintListToolbar(table, 'list-toolbar');

    });


    /* The id rides on each row as `data-experiment-id`: the table shows no ID column, an
       id being an address rather than a label, and reading a cell by index is what broke
       the moment a column moved. */
    function get_selected_experiment_ids(table) {
        var experiment_ids = [];
        table.rows('.selected').nodes().each(function (row) {
            experiment_ids.push(row.getAttribute('data-experiment-id'));
        });
        return experiment_ids.join(",");
    }


    function export_data(table, mutation_type){
        exp_ids = get_selected_experiment_ids(table);
        if (exp_ids == ''){
            swal("", "Please select experiments and try again.", "warning");
            return;
        }
        var url = "/export/";
        var params = {
                    'mut_type': mutation_type,
                    'project_id': project_id,
                    'experiment_ids': exp_ids
                };
        var form = $('<form method="GET" action="' + url + '">');
        $.each(params, function(k, v) {
            form.append($('<input type="hidden" name="' + k +
                    '" value="' + v + '">'));
        });
        $('body').append(form);
        form.submit();
    }

    function export_experiment_index(table){
        var exp_ids = get_selected_experiment_ids(table);
        if (exp_ids == ''){
            swal("", "Please select experiments and try again.", "warning");
            return;
        }
        var url = "/export/experiment_index";
        var params = {
                    'project_id': project_id,
                    'experiment_ids': exp_ids
                };
        var form = $('<form method="GET" action="' + url + '">');
        $.each(params, function(k, v) {
            form.append($('<input type="hidden" name="' + k +
                    '" value="' + v + '">'));
        });
        $('body').append(form);
        form.submit();
    }

