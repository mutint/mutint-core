/**
 * The needle plot on /stats, drawing an experiment's mutations along a genome axis.
 *
 * Created by dgosting on 6/24/16; the axis was made to describe the actual reference later.
 * It used to hardcode `maxCoord: 5000000` -- roughly E. coli, and simply wrong for any other
 * genome -- while the points it was given carried no sequence name, so a multi-contig
 * reference drew every contig on top of itself. Neither failed loudly: the plot rendered, it
 * was just not about this genome. The axis now comes from the reference, and the server sends
 * one contig's points (aledb_stats/util.py `needle_plot_axis`).
 */
var NEEDLE_PLOT = NEEDLE_PLOT || (function () {
    var _data = [];
    var _axis = {};

    /** The largest coordinate in the data, as a last resort for the axis. */
    function largestCoordinate(data) {
        var max = 0;
        for (var i = 0; i < data.length; i++) {
            var coord = parseInt(data[i].coord, 10);
            if (!isNaN(coord) && coord > max) { max = coord; }
        }
        // A little room past the rightmost mutation, so it is not drawn on the axis itself.
        return Math.ceil(max * 1.02) || 1;
    }

    return {
        init: function (mutationData, axis) {
            _data = mutationData || [];
            _axis = axis || {};
        },
        create_muts_needle_plot: function () {
            var yourDiv = document.getElementById('plot');
            var mutneedles = require("muts-needle-plot");
            // The reference's own length when there is a stored reference, and otherwise the
            // data's own extent -- which is still a truer axis than a constant, and is what an
            // experiment imported from bare .gd files with no reference gets.
            var maxCoord = _axis.length || largestCoordinate(_data);
            var instance = new mutneedles({
                maxCoord: maxCoord,
                minCoord: 0,
                targetElement: yourDiv,
                mutationData: _data,
                colorMap: {},
                legends: {
                    x: _axis.contig ? ("Position on " + _axis.contig) : "Position",
                    y: "Number of Recorded Mutations"
                }
            });
            return instance;
        }
    };
}());
