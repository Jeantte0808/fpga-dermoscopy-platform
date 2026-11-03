set kit [file dirname [file dirname [file normalize [info script]]]]
source [file join $kit board after_opt.tcl]
# Only the intentional asynchronous assertion pins of custom reset chains.
set pins {}
foreach c [get_cells -hier -filter {ASYNC_REG == TRUE}] {
 if {[regexp {(^|/)(axi_reset_pipe|io_reset_pipe|ui_reset_pipe|net_reset_pipe|s_reset_pipe|d_reset_pipe)_reg\[[0-9]+\]$} [get_property NAME $c]]} {
  foreach p [get_pins -of_objects $c -filter {REF_PIN_NAME == PRE || REF_PIN_NAME == CLR}] {lappend pins $p}
 }
}
if {[llength $pins]<20 || [llength $pins]>45} {error "Custom reset-chain pin count changed"}
set_false_path -to $pins
set rx [get_cells {phy_rx/rgmii_rxd_i[0].IDDR_rxd}]
set region [get_clock_regions -of_objects [get_sites -of_objects $rx]]
if {[llength $region]!=1} {error "Cannot identify real RGMII region"}
set regs [get_cells -hier -regexp {^io_reset_pipe_reg\[[0-9]+\]$}]
if {[llength $regs]!=4} {error "IO reset chain changed"}
create_pblock io_reset_near_rx
set name [get_property NAME $region]
resize_pblock [get_pblocks io_reset_near_rx] -add "CLOCKREGION_${name}:CLOCKREGION_${name}"
add_cells_to_pblock [get_pblocks io_reset_near_rx] $regs
set f [open [file join $kit verification BOARD_ASYNC_RESET_PINS.txt] w]
puts $f [join $pins \n];close $f
