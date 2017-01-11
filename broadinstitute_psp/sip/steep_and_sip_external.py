"""
steep_and_sip_external.py

Run steep.py (compute similarities) and sip.py (compute connectivities) on an
external gct file. The required inputs are a path to a gct of external profiles
(probes x samples), a path to a gct of internal profiles, and a path to a gct
of the pre-computed similarity matrix of the internal profiles against
themselves.

"""

import logging
import sys
import argparse
import pandas as pd

import broadinstitute_psp.utils.setup_logger as setup_logger
import broadinstitute_psp.steep.steep as steep
import broadinstitute_psp.sip.sip as sip
import broadinstitute_cmap.io.pandasGEXpress.GCToo as GCToo
import broadinstitute_cmap.io.pandasGEXpress.parse_gct as pg
import broadinstitute_cmap.io.pandasGEXpress.write_gct as wg
import broadinstitute_psp.utils.psp_utils as utils

__author__ = "Lev Litichevskiy"
__email__ = "lev@broadinstitute.org"

logger = logging.getLogger(setup_logger.LOGGER_NAME)

SIMILARITY_METRIC_FIELD = "similarity_metric"
CONNECTIVITY_METRIC_FIELD = "connectivity_metric"
QUERY_FIELD_NAME = "query_field"
TARGET_FIELD_NAME = "target_field"
SEPARATOR = ":"


def build_parser():
    """Build argument parser."""

    parser = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    # Required args
    parser.add_argument("--external_gct_path", "-e", required=True,
                        help="path to gct file of external profiles")
    parser.add_argument("--internal_gct_path", "-i", required=True,
                        help="path to gct file of internal profiles")
    parser.add_argument("--bg_gct_path", "-b", required=True,
                        help="path to background similarity gct file")

    # Optional args
    parser.add_argument("--out_steep_name", "-ost", default="steep_output.gct",
                        help="what to name the output similarity file")
    parser.add_argument("--out_sip_name", "-osi", default="sip_output.gct",
                        help="what to name the output connectivity file")
    parser.add_argument("--similarity_metric", "-s", default="spearman",
                        choices=["spearman", "pearson"],
                        help="metric to use for comparing sample profiles")
    parser.add_argument("--connectivity_metric", "-c", default="ks_test",
                        choices=["ks_test", "percentile_score"],
                        help="metric to use for computing connectivity")
    parser.add_argument("--psp_config_path", "-p",
                        default="~/psp_production.cfg",
                        help="filepath to PSP config file")
    parser.add_argument("--fields_to_aggregate_in_test_gct_queries", "-tfq",
                        nargs="+", default=["pert_id", "cell_id", "pert_time"],
                        help="list of metadata fields in the columns of the test gct to aggregate")
    parser.add_argument("--fields_to_aggregate_in_test_gct_targets", "-tft",
                        nargs="+", default=["pert_id", "cell_id", "pert_time"],
                        help="list of metadata fields in the rows of the test gct to aggregate")
    parser.add_argument("--fields_to_aggregate_in_bg_gct", "-bf",
                        nargs="+", default=["pert_id", "cell_id", "pert_time"],
                        help="list of metadata fields in the bg gct to aggregate")
    parser.add_argument("--verbose", "-v", action="store_true", default=False,
                        help="whether to increase the # of messages reported")

    return parser


def main(args):

    #----------STEEP----------#

    # Parse input gcts
    external_gct = utils.read_gct_and_config_file(args.external_gct_path, args.psp_config_path)[0]
    internal_gct = utils.read_gct_and_config_file(args.internal_gct_path, args.psp_config_path)[0]
    bg_gct = utils.read_gct_and_config_file(args.bg_gct_path, args.psp_config_path)[0]

    # Compute similarity between external and internal profiles
    sim_df = steep.compute_similarity_bw_two_dfs(internal_gct.data_df,
                                                 external_gct.data_df,
                                                 args.similarity_metric)

    # Row metadata is from gct1, column metadata is from gct2
    row_metadata_for_sim_df = internal_gct.col_metadata_df
    col_metadata_for_sim_df = external_gct.col_metadata_df

    # Append column to both metadata_dfs indicating which similarity_metric was used
    row_metadata_for_sim_df[SIMILARITY_METRIC_FIELD] = args.similarity_metric
    col_metadata_for_sim_df[SIMILARITY_METRIC_FIELD] = args.similarity_metric

    # Assemble similarity gct
    sim_gct = GCToo.GCToo(sim_df, row_metadata_for_sim_df, col_metadata_for_sim_df, make_multiindex=True)

    # Write output similarity gct
    wg.write(sim_gct, args.out_steep_name, data_null="NaN", metadata_null="NA", filler_null="NA")

    #----------SIP----------#

    # Create an aggregated metadata field for index and columns of both gcts
    # and sort by that field
    (test_df, bg_df) = sip.prepare_multi_index_dfs(
        sim_gct.multi_index_df, bg_gct.multi_index_df,
        args.fields_to_aggregate_in_test_gct_queries,
        args.fields_to_aggregate_in_test_gct_targets,
        args.fields_to_aggregate_in_bg_gct,
        QUERY_FIELD_NAME, TARGET_FIELD_NAME, SEPARATOR)

    # Check symmetry
    (is_test_df_sym, is_bg_df_sym) = sip.check_symmetry(sim_gct.multi_index_df, bg_gct.multi_index_df)

    # Compute connectivity
    (conn_mi_df, signed_conn_mi_df) = sip.compute_connectivities(
        test_df, bg_df, QUERY_FIELD_NAME, TARGET_FIELD_NAME, TARGET_FIELD_NAME,
        args.connectivity_metric, is_test_df_sym)

    # Convert multi-index to component dfs in order to write output gct
    (signed_data_df, signed_row_metadata_df, signed_col_metadata_df) = GCToo.multi_index_df_to_component_dfs(signed_conn_mi_df, rid=TARGET_FIELD_NAME, cid=QUERY_FIELD_NAME)

    # Append to queries a new column saying what connectivity metric was used
    sip.add_connectivity_metric_to_metadata(signed_col_metadata_df, args.connectivity_metric, CONNECTIVITY_METRIC_FIELD)
    sip.add_connectivity_metric_to_metadata(signed_row_metadata_df, args.connectivity_metric, CONNECTIVITY_METRIC_FIELD)

    # Create connectivity gct and write it to file
    conn_gct = GCToo.GCToo(data_df=signed_data_df, row_metadata_df=signed_row_metadata_df, col_metadata_df=signed_col_metadata_df)
    wg.write(conn_gct, args.out_sip_name, data_null="NaN", filler_null="NaN", metadata_null="NaN")


if __name__ == "__main__":
    args = build_parser().parse_args(sys.argv[1:])
    setup_logger.setup(verbose=args.verbose)

    main(args)