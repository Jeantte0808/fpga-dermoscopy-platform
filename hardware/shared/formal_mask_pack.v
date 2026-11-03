`timescale 1ns/1ps
// One C16 logit word per pixel, raster order. Only lane 0 is logical.
// Signed q >= 1 maps to 255. 16 mask bytes packed low byte first.
// A malformed input quarantines the frame until coordinated reset.
module formal_mask_pack(
 input clk,reset,input in_valid,output in_ready,input [127:0] in_data,
 output reg out_valid,input out_ready,output reg [127:0] out_data,
 output reg [11:0] out_index,output reg out_last,output reg failed,
 output reg frame_done
);
 reg [15:0] pixel;
 reg [127:0] pack;
 reg ending;
 wire invalid_word=(in_data[127:8]!=0)||(in_data[7:0]==8'h80);
 assign in_ready=!failed&&!ending&&(!out_valid||out_ready);
 always @(posedge clk)begin
  if(reset)begin
   pixel<=0;pack<=0;ending<=0;out_valid<=0;out_data<=0;
   out_index<=0;out_last<=0;failed<=0;frame_done<=0;
  end else begin
   frame_done<=0;
   if(out_valid&&out_ready)begin
    out_valid<=0;
    if(out_last)begin ending<=0;pixel<=0;pack<=0;frame_done<=1;end
   end
   if(in_valid&&in_ready)begin
    if(invalid_word)begin failed<=1;out_valid<=0;end
    else begin
     pack[pixel[3:0]*8+:8]<=($signed(in_data[7:0])>=1)?8'hff:8'h00;
     pixel<=pixel+1'b1;
     if(pixel[3:0]==15)begin
      out_data<={($signed(in_data[7:0])>=1)?8'hff:8'h00,pack[119:0]};
      out_index<=pixel[15:4];out_last<=pixel==16'hffff;out_valid<=1;
      if(pixel==16'hffff)ending<=1;
     end
    end
   end
  end
 end
endmodule
