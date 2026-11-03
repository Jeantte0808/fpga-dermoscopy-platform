set kit [file dirname [file dirname [file normalize [info script]]]]
report_timing_summary -report_unconstrained -file [file join $kit verification BOARD_TIMING.rpt]
report_utilization -file [file join $kit verification BOARD_UTILIZATION.rpt]
report_drc -file [file join $kit verification BOARD_DRC.rpt]
report_cdc -details -file [file join $kit verification BOARD_CDC.rpt]
report_bus_skew -file [file join $kit verification BOARD_BUS_SKEW.rpt]
# Do not generate a pass here: external RGMII budgets still require review.
set f [open [file join $kit verification BOARD_ROUTE_STATUS.json] w]
puts $f "{\"routed\":true,\"bit_generated_by_agent\":false,\"JTAG_accessed\":false,\"external_RGMII_IO_timing_complete\":false,\"whole_board_accepted\":false}"
close $f
