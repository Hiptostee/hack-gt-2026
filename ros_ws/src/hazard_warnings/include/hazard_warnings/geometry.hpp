#pragma once

// ROS-independent, full-resolution Stage A geometry. No interpolation/hole filling.
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace hazards {
struct Point { double x{}, y{}, z{}; };
struct Transform {
  std::array<double, 9> r{1,0,0, 0,1,0, 0,0,1};
  Point t;
  Point rotate(Point p) const {
    return {r[0]*p.x+r[1]*p.y+r[2]*p.z,
      r[3]*p.x+r[4]*p.y+r[5]*p.z, r[6]*p.x+r[7]*p.y+r[8]*p.z};
  }
  Point apply(Point p) const {p=rotate(p); return {p.x+t.x,p.y+t.y,p.z+t.z};}
};
inline Transform rpy(double roll, double pitch, double yaw, Point t={}) {
  const double a=std::cos(roll), b=std::sin(roll), c=std::cos(pitch),
    d=std::sin(pitch), e=std::cos(yaw), f=std::sin(yaw);
  return {{e*c,e*d*b-f*a,e*d*a+f*b, f*c,f*d*b+e*a,f*d*a-e*b, -d,c*b,c*a},t};
}
struct Config {
  double half_width{0.4}, torso_min{0.8}, head_min{1.4}, head_max{1.9};
  double min_depth{0.2}, max_depth{4.0}, caution{1.5}, urgent{0.8};
  double cluster_gap{0.08}, min_valid_fraction{0.5};
  int min_support{3}, urgent_support{12};
};
struct Intrinsics {
  unsigned width{}, height{};
  double fx{}, fy{}, cx{}, cy{};
  std::array<double,5> distortion{};
  // Brown-Conrady inverse, also valid for rectified/zero-distortion frames.
  Point ray(unsigned u, unsigned v) const {
    const double xd=(u-cx)/fx, yd=(v-cy)/fy;
    double x=xd,y=yd;
    for (int i=0;i<10;++i) {
      const double r2=x*x+y*y;
      const double radial=1+distortion[0]*r2+distortion[1]*r2*r2+distortion[4]*r2*r2*r2;
      if (!std::isfinite(radial) || std::abs(radial)<1e-8) return {NAN,NAN,NAN};
      x=(xd-2*distortion[2]*x*y-distortion[3]*(r2+2*x*x))/radial;
      y=(yd-distortion[2]*(r2+2*y*y)-2*distortion[3]*x*y)/radial;
    }
    return {x,y,1};
  }
};
inline std::vector<float> decode_depth(const std::vector<uint8_t>& bytes,
  unsigned width,unsigned height,unsigned step,const std::string& encoding,bool big_endian,
  double scale16)
{
  const size_t size=encoding=="16UC1"?2:encoding=="32FC1"?4:0;
  if (!size || !width || !height || width>1920 || height>1080 ||
      step<width*size || bytes.size()!=static_cast<size_t>(step)*height ||
      !std::isfinite(scale16) || scale16<=0) throw std::invalid_argument("invalid depth layout/units");
  std::vector<float> result(static_cast<size_t>(width)*height);
  for(unsigned v=0;v<height;++v) for(unsigned u=0;u<width;++u) {
    const auto* p=&bytes[static_cast<size_t>(v)*step+u*size];
    uint32_t bits=0;
    for(size_t b=0;b<size;++b) bits|=uint32_t(p[b])<<(8*(big_endian?size-1-b:b));
    float value;
    if(size==2) value=static_cast<float>(bits*scale16);
    else std::memcpy(&value,&bits,4); // IEEE float bytes now in host order.
    result[static_cast<size_t>(v)*width+u]=value;
  }
  return result;
}
struct Candidate {Point point; int support{}; int zone{};};
struct Geometry {
  std::vector<Candidate> candidates;
  bool coverage_ok{false};
};
inline int zone(Point p,const Config& c) {
  return (p.z>=c.head_min?3:0)+(p.y>c.half_width/3?0:p.y<-c.half_width/3?2:1);
}
inline double separation(Point a,Point b) {
  return std::sqrt((a.x-b.x)*(a.x-b.x)+(a.y-b.y)*(a.y-b.y)+(a.z-b.z)*(a.z-b.z));
}
inline Geometry detect(const std::vector<float>& depth,const Intrinsics& k,
  const Transform& body,const Config& c)
{
  if(depth.size()!=static_cast<size_t>(k.width)*k.height || !(k.fx>0 && k.fy>0))
    throw std::invalid_argument("invalid intrinsics");
  std::vector<Point> points(depth.size());
  std::vector<uint8_t> occupied(depth.size(),0),visited(depth.size(),0);
  std::array<int,6> roi{}, valid{};
  for(unsigned v=0;v<k.height;++v) for(unsigned u=0;u<k.width;++u) {
    const size_t i=static_cast<size_t>(v)*k.width+u;
    const Point ray=k.ray(u,v);
    const Point near=body.apply({ray.x*c.caution,ray.y*c.caution,c.caution});
    const bool in_roi=std::abs(near.y)<=c.half_width && near.z>=c.torso_min && near.z<=c.head_max;
    const bool good=std::isfinite(depth[i]) && depth[i]>=c.min_depth && depth[i]<=c.max_depth;
    if(in_roi) {const int z=zone(near,c); ++roi[z]; if(good) ++valid[z];}
    if(!good) continue;
    const Point p=body.apply({ray.x*depth[i],ray.y*depth[i],depth[i]});
    if(p.x>=c.min_depth && p.x<=c.max_depth && std::abs(p.y)<=c.half_width &&
        p.z>=c.torso_min && p.z<=c.head_max) {points[i]=p; occupied[i]=1;}
  }
  Geometry result;
  result.coverage_ok=true;
  for(int z=0;z<6;++z) if(roi[z]<4 || double(valid[z])/roi[z]<c.min_valid_fraction)
    result.coverage_ok=false;
  std::vector<size_t> stack;
  for(size_t start=0;start<depth.size();++start) {
    if(!occupied[start] || visited[start]) continue;
    stack.clear(); stack.push_back(start); visited[start]=1;
    std::array<int,6> count{};
    std::array<Point,6> closest;
    for(auto& p:closest) p.x=std::numeric_limits<double>::infinity();
    while(!stack.empty()) {
      size_t i=stack.back(); stack.pop_back();
      const int z=zone(points[i],c); ++count[z];
      if(points[i].x<closest[z].x) closest[z]=points[i];
      const int x=i%k.width,y=i/k.width;
      for(int dy=-1;dy<=1;++dy) for(int dx=-1;dx<=1;++dx) {
        const int nx=x+dx,ny=y+dy;
        if(nx<0 || ny<0 || nx>=int(k.width) || ny>=int(k.height)) continue;
        const size_t j=static_cast<size_t>(ny)*k.width+nx;
        if(!visited[j] && occupied[j] && separation(points[i],points[j])<=c.cluster_gap) {
          visited[j]=1; stack.push_back(j);
        }
      }
    }
    for(int z=0;z<6;++z) if(count[z]>=c.min_support) result.candidates.push_back({closest[z],count[z],z});
  }
  return result;
}
} // namespace hazards
