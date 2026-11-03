set kit [file dirname [file dirname [file normalize [info script]]]]
# Scope only asynchronous single-bit first stages. Gray buses stay timed.
set ack [get_cells -hier -regexp {.*ack_sync_reg\[0\]}]
if {[llength $ack]!=2} {error "Expected two actual packet ACK synchronizers"}
set_false_path -to $ack
set fault [get_cells -hier -regexp {.*gateway_error_sync_reg\[0\]}]
if {[llength $fault]!=1} {error "Missing gateway-fault first stage"}
set_false_path -to $fault
set led [get_cells -hier -filter {NAME =~ *led_meta_reg*}]
if {[llength $led]} {set_false_path -to $led}
set count 0
set g [open [file join $kit verification BOARD_GRAY_CONSTRAINTS.tsv] w]
puts $g "instance\tsource_ns\tdestination_ns"
foreach inst [get_cells -hier -filter {ORIG_REF_NAME == xpm_cdc_gray || REF_NAME =~ xpm_cdc_gray*}] {
 set n [get_property NAME $inst]
 set src [get_cells -quiet -hier -filter "NAME =~ $n/src_gray_ff_reg*"]
 set dst {}
 foreach c [get_cells -quiet -hier -filter "NAME =~ $n/dest_graysync_ff_reg*"] {
  if {[string first {dest_graysync_ff_reg[0]} [get_property NAME $c]]>=0} {lappend dst $c}
 }
 if {[llength $src]==0&&[llength $dst]==0} {continue}
 if {[llength $src]!=7||[llength $dst]!=7} {error "Board Gray pointer width mismatch: $n"}
 set sc [get_clocks -of_objects [get_pins -of_objects $src -filter {REF_PIN_NAME == C}]]
 set dc [get_clocks -of_objects [get_pins -of_objects $dst -filter {REF_PIN_NAME == C}]]
 if {[llength $sc]!=1||[llength $dc]!=1} {error "Board Gray clocks missing: $n"}
 set sp [get_property PERIOD $sc];set dp [get_property PERIOD $dc]
 if {abs($sp-8)>0.01&&abs($sp-10)>0.01} {error "Unexpected board source clock $sp"}
 if {abs($dp-8)>0.01&&abs($dp-10)>0.01} {error "Unexpected board destination clock $dp"}
 set_max_delay -from $src -to $dst -datapath_only $sp
 set_bus_skew -from $src -to $dst [expr {min($sp,$dp)}]
 puts $g "$n\t$sp\t$dp";incr count
}
close $g
if {$count!=4} {error "Expected four board Gray crossings"}
