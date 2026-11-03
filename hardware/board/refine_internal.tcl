# Offline physical exploration from the existing placed netlist. No RTL change.
set kit [file dirname [file dirname [file normalize [info script]]]]
cd $kit
set_param general.maxThreads 4
open_checkpoint hw/formal_ethernet_system.runs/impl_1/formal_ethernet_top_placed.dcp
# These are asynchronous ASSERT / synchronous RELEASE reset chains. Their
# PRE/CLR inputs are intentionally asynchronous; D/Q propagation remains timed.
# No ordinary data, Gray pointer, or destination consumer reset is excluded.
set reset_pins {}
foreach cell [get_cells -hier -filter {ASYNC_REG == TRUE}] {
 set name [get_property NAME $cell]
 if {[regexp {(^|/)(axi_reset_pipe|io_reset_pipe|ui_reset_pipe|net_reset_pipe|s_reset_pipe|d_reset_pipe)_reg\[[0-9]+\]$} $name]} {
  foreach pin [get_pins -of_objects $cell -filter {REF_PIN_NAME == PRE || REF_PIN_NAME == CLR}] {lappend reset_pins $pin}
 }
}
if {[llength $reset_pins]<20 || [llength $reset_pins]>40} {error "Unexpected reset-chain endpoints: [llength $reset_pins]"}
set_false_path -to $reset_pins
set f [open verification/REFINE_ASYNC_RESET_PINS.txt w]
puts $f [join $reset_pins \n]
close $f
phys_opt_design -directive AggressiveExplore
route_design -directive Explore
report_timing_summary -report_unconstrained -file verification/REFINE_TIMING.rpt
report_utilization -file verification/REFINE_UTILIZATION.rpt
report_cdc -details -file verification/REFINE_CDC.rpt
report_drc -file verification/REFINE_DRC.rpt
report_bus_skew -file verification/REFINE_BUS_SKEW.rpt
write_checkpoint -force verification/refinement_01.dcp
puts "BOARD_REFINEMENT_REPORTED: no timing acceptance implied; no bit/JTAG"
close_design
exit
