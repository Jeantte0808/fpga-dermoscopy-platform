set_property PACKAGE_PIN W19 [get_ports FPGA_CLK]
set_property IOSTANDARD LVCMOS33 [get_ports FPGA_CLK]
create_clock -period 20.000 -name board_clk [get_ports FPGA_CLK]
set_property PACKAGE_PIN D21 [get_ports reset_n]
set_property IOSTANDARD LVCMOS33 [get_ports reset_n]
set_property PACKAGE_PIN U22 [get_ports {led[0]}]
set_property PACKAGE_PIN V22 [get_ports {led[1]}]
set_property PACKAGE_PIN W21 [get_ports {led[2]}]
set_property PACKAGE_PIN W22 [get_ports {led[3]}]
set_property PACKAGE_PIN Y21 [get_ports {led[4]}]
set_property PACKAGE_PIN Y22 [get_ports {led[5]}]
set_property PACKAGE_PIN N13 [get_ports {led[6]}]
set_property PACKAGE_PIN N17 [get_ports {led[7]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[*]}]
set_false_path -from [get_ports reset_n]
set_false_path -to [get_ports {led[*]}]
# MIG supplies DDR package pins and timing constraints from the official .prj.
# The multi-bit diagnostic bus is read only after it has stopped changing.
#set UnusedPin
set_property BITSTREAM.CONFIG.UNUSEDPIN Pullnone [current_design]

#reset active low,S0
set_property PACKAGE_PIN D21 [get_ports reset_n]
set_property IOSTANDARD LVCMOS33 [get_ports reset_n]

##led pin
set_property PACKAGE_PIN U22 [get_ports {led[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[0]}]
set_property PACKAGE_PIN V22 [get_ports {led[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[1]}]
set_property PACKAGE_PIN W21 [get_ports {led[2]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[2]}]
set_property PACKAGE_PIN W22 [get_ports {led[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[3]}]
set_property PACKAGE_PIN Y21 [get_ports {led[4]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[4]}]
set_property PACKAGE_PIN Y22 [get_ports {led[5]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[5]}]
set_property PACKAGE_PIN N13 [get_ports {led[6]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[6]}]
set_property PACKAGE_PIN N17 [get_ports {led[7]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[7]}]

#eth_reset_n
#set_property PACKAGE_PIN V22 [get_ports eth_reset_n]
#set_property IOSTANDARD LVCMOS33 [get_ports eth_reset_n]

#rgmii_tx
set_property PACKAGE_PIN AA19 [get_ports rgmii_txen]
set_property PACKAGE_PIN AB20 [get_ports {rgmii_txd[0]}]

set_property IOSTANDARD LVCMOS33 [get_ports rgmii_tx_clk]
set_property IOSTANDARD LVCMOS33 [get_ports rgmii_txen]
set_property IOSTANDARD LVCMOS33 [get_ports {rgmii_txd[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {rgmii_txd[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {rgmii_txd[2]}]
set_property IOSTANDARD LVCMOS33 [get_ports {rgmii_txd[3]}]

#rgmii_rx
set_property PACKAGE_PIN Y18 [get_ports rgmii_rx_clk_i]
set_property PACKAGE_PIN P20 [get_ports {rgmii_rxd[0]}]
set_property PACKAGE_PIN N15 [get_ports {rgmii_rxd[1]}]
set_property PACKAGE_PIN AA18 [get_ports {rgmii_rxd[2]}]
set_property PACKAGE_PIN AB18 [get_ports {rgmii_rxd[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports rgmii_rx_clk_i]
set_property IOSTANDARD LVCMOS33 [get_ports rgmii_rxdv]
set_property IOSTANDARD LVCMOS33 [get_ports {rgmii_rxd[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {rgmii_rxd[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {rgmii_rxd[2]}]
set_property IOSTANDARD LVCMOS33 [get_ports {rgmii_rxd[3]}]

set_property CLOCK_DEDICATED_ROUTE FALSE [get_nets rgmii_rx_clk_i]

#eth_reset_n
set_property IOSTANDARD LVCMOS33 [get_ports eth_rst_n]

set_property PACKAGE_PIN W20 [get_ports {rgmii_txd[3]}]
set_property PACKAGE_PIN AB22 [get_ports {rgmii_txd[2]}]
set_property PACKAGE_PIN Y19 [get_ports {rgmii_txd[1]}]
set_property PACKAGE_PIN P14 [get_ports eth_rst_n]
set_property PACKAGE_PIN T20 [get_ports rgmii_rxdv]
set_property PACKAGE_PIN AB21 [get_ports rgmii_tx_clk]

# Fixed 1Gbps PHY receive clock; generated clock remains 125MHz +90deg.
# Ethernet input clock is supplied exactly once by clk_wiz_eth.xdc (8ns).
