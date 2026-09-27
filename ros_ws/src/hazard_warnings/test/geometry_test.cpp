#include <cstdlib>
#include <iostream>
#include "hazard_warnings/policy.hpp"
using namespace hazards;
void check(bool ok,const char* name) {if(!ok){std::cerr<<"FAIL: "<<name<<"\n"; std::exit(1);}}
int main() {
  const Transform mount=rpy(-M_PI/2,0,-M_PI/2,{0,0,1.3});
  Point center=mount.apply({0,0,1});
  check(std::abs(center.x-1)<1e-6 && std::abs(center.z-1.3)<1e-6,"optical -> body transform");
  check(mount.apply({0.2,0,1}).y<0,"optical right is body right");
  auto d16=decode_depth({0xe8,0x03,0,0,99,99},2,1,6,"16UC1",false,0.001);
  check(std::abs(d16[0]-1)<1e-6 && d16[1]==0,"16UC1 units and padded rows");
  auto be=decode_depth({3,0xe8},1,1,2,"16UC1",true,0.001);
  auto f32=decode_depth({0x3f,0x80,0,0},1,1,4,"32FC1",true,0.001);
  check(be[0]==1 && f32[0]==1,"big endian integer and float");
  bool rejected=false; try {decode_depth({0},1,1,2,"16UC1",false,0.001);} catch(...) {rejected=true;}
  check(rejected,"short image refused");
  Intrinsics k{80,60,60,60,40,30,{}}; Config c;
  std::vector<float> depth(k.width*k.height,3.0f);
  auto background=detect(depth,k,mount,c);
  check(background.coverage_ok,"background supplies valid coverage");
  Policy policy; policy.update(background,1000000000,c); policy.update(background,1060000000,c);
  check(policy.events(1060000000).empty(),"distant wall is not caution");
  // Three-pixel-wide upright fixture, retained at full resolution.
  for(int y=12;y<27;++y) for(int x=39;x<42;++x) depth[y*80+x]=1.2f;
  auto thin=detect(depth,k,mount,c);
  policy.reset(); policy.update(thin,2000000000,c);
  check(policy.events(2000000000).empty(),"ordinary requires two frames");
  policy.update(thin,2060000000,c);
  check(!policy.events(2060000000).empty(),"thin foreground not averaged into background");
  check(policy.events(2400000000).empty(),"observation expiry");
  std::fill(depth.begin(),depth.end(),3.0f); depth[20*80+40]=0.5;
  auto noise=detect(depth,k,mount,c); policy.reset(); policy.update(noise,3000000000,c);
  check(policy.events(3000000000).empty(),"isolated noise rejected");
  for(int y=8;y<28;++y) for(int x=37;x<44;++x) depth[y*80+x]=0.6f;
  auto close=detect(depth,k,mount,c); policy.reset(); policy.update(close,4000000000,c);
  check(!policy.events(4000000000).empty() && policy.events(4000000000)[0].urgent,"supported urgent bypass");
  std::fill(depth.begin(),depth.end(),NAN);
  auto missing=detect(depth,k,mount,c);
  check(!missing.coverage_ok && missing.candidates.empty(),"invalid depth is unavailable, not clear");
  policy.update(missing,4060000000,c);
  check(!policy.events(4060000000).empty(),"missing samples cannot prove clearance");
  check(policy.events(4260000000).empty(),"missing evidence still expires");
  // Controlled policy fixture for association, hysteresis and escalation.
  Geometry g; g.coverage_ok=true; g.candidates={{{1.4,0,1.6},20,4}};
  policy.reset(); policy.update(g,5000000000,c); policy.update(g,5060000000,c);
  auto id=policy.events(5060000000)[0].id;
  g.candidates[0].point.x=1.6; policy.update(g,5120000000,c);
  check(policy.events(5120000000)[0].id==id,"stable track inside hysteresis band");
  g.candidates[0].point.x=1.75;
  for(int n=1;n<=4;++n) {policy.update(g,5120000000+n*60000000LL,c);
    check(!policy.events(5120000000+n*60000000LL).empty(),"five-frame clear hysteresis");}
  policy.update(g,5420000000,c); check(policy.events(5420000000).empty(),"observed exit clears");
  std::cout<<"Stage A geometry/policy checks passed\n";
}
