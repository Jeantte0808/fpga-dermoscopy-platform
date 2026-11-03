`timescale 1ns/1ps
// Fixed 256x256 RGB byte stream -> signed INT8 HWC C16, 13 zero tail lanes.
// Table is the authoritative 768-byte R/G/B export; no arithmetic approximation.
module formal_rgb_lut(
 input clk,reset,cfg_valid,input [7:0] cfg_data,input cfg_commit,
 output cfg_ready,output reg configured,cfg_error,
 input in_valid,input [7:0] in_data,input in_last,output in_ready,
 output reg out_valid,input out_ready,output reg [127:0] out_data,
 output reg out_last,output reg done,failed
);
 reg [7:0] table_data[0:767];reg [9:0] count;reg [1:0] component;
 reg [7:0] red,green;reg [16:0] pixels;reg finished;
 assign cfg_ready=!configured&&!cfg_error&&count<768;
 assign in_ready=configured&&!failed&&!finished&&(!out_valid||out_ready);
 always @(posedge clk)begin
 if(reset)begin count<=0;configured<=0;cfg_error<=0;component<=0;red<=0;green<=0;pixels<=0;finished<=0;out_valid<=0;out_data<=0;out_last<=0;done<=0;failed<=0;end
 else begin
 done<=0;
 if(cfg_valid&&cfg_ready)begin table_data[count]<=cfg_data;count<=count+1;end
 if(cfg_commit&&!configured)begin if(count==768&&!cfg_valid&&!cfg_error)configured<=1;else cfg_error<=1;end
 if(out_valid&&out_ready)begin out_valid<=0;if(out_last)done<=1;end
 if(in_valid&&in_ready)begin
 if(in_last!=(pixels==65535&&component==2))failed<=1;
 case(component)
 0:begin red<=table_data[{2'd0,in_data}];component<=1;end
 1:begin green<=table_data[10'd256+in_data];component<=2;end
 2:begin out_data<={104'd0,table_data[10'd512+in_data],green,red};out_valid<=1;out_last<=pixels==65535;
 pixels<=pixels+1;component<=0;if(pixels==65535)finished<=1;end
 endcase end
 end end
endmodule
