# Place the already existing IO reset synchronizer near its IDDR consumers.
# This changes placement/constraints only, never RTL or numerical rules.
set kit [file dirname [file dirname [file normalize [info script]]]]
cd $kit
set_param general.maxThreads 4
open_checkpoint hw/formal_ethernet_system.runs/impl_1/formal_ethernet_top_opt.dcp
set reset_pins {}
foreach cell [get_cells -hier -filter {ASYNC_REG == TRUE}] {
 set name [get_property NAME $cell]
 if {[regexp {(^|/)(axi_reset_pipe|io_reset_pipe|ui_reset_pipe|net_reset_pipe|s_reset_pipe|d_reset_pipe)_reg\[[0-9]+\]$} $name]} {
  foreach pin [get_pins -of_objects $cell -filter {REF_PIN_NAME == PRE || REF_PIN_NAME == CLR}] {lappend reset_pins $pin}
 }
}
if {[llength $reset_pins]<20 || [llength $reset_pins]>40} {error "Unexpected reset-chain endpoints"}
set_false_path -to $reset_pins
set rx [get_cells {phy_rx/rgmii_rxd_i[0].IDDR_rxd}]
if {[llength $rx]!=1} {error "Cannot identify actual RGMII input register"}
set regions [get_clock_regions -of_objects [get_sites -of_objects $rx]]
if {[llength $regions]!=1} {error "Cannot identify actual RGMII clock region"}
set regs [get_cells -hier -regexp {^io_reset_pipe_reg\[[0-9]+\]$}]
if {[llength $regs]!=4} {error "IO reset chain width changed"}
create_pblock io_reset_near_rx
set region_name [get_property NAME $regions]
resize_pblock [get_pblocks io_reset_near_rx] -add "CLOCKREGION_$region_name:CLOCKREGION_$region_name"
# Keep only these four flops in the consumer's clock region. Other cells may
# still use the region; no exclusive placement fence or unsupported soft flag.
add_cells_to_pblock [get_pblocks io_reset_near_rx] $regs
set f [open verification/IO_PLACE_REGION.txt w]
puts $f "region=[get_property NAME $regions] cells=$regs"
close $f
place_design -directive Explore
phys_opt_design -directive AggressiveExplore
route_design -directive Explore
report_timing_summary -report_unconstrained -file verification/IO_PLACE_TIMING.rpt
report_utilization -file verification/IO_PLACE_UTILIZATION.rpt
report_cdc -details -file verification/IO_PLACE_CDC.rpt
report_drc -file verification/IO_PLACE_DRC.rpt
report_bus_skew -file verification/IO_PLACE_BUS_SKEW.rpt
write_checkpoint -force verification/io_placement_01.dcp
puts "BOARD_IO_PLACEMENT_REPORTED: inspect all reports; no bit/JTAG"
close_design
exit
