#pragma once
#include "hazard_warnings/geometry.hpp"

namespace hazards {
struct Event {std::string id; int zone{}; bool urgent{}; double distance{}; int64_t observed{};};
class Policy {
  struct Track {
    Point point; int hits{},clear{}; bool active{},urgent{};
    int64_t observed{}; unsigned id{};
  };
  std::array<Track,6> tracks_{};
  unsigned next_id_{0};
public:
  void reset() {tracks_={};}
  void update(const Geometry& geometry,int64_t stamp,const Config& c) {
    std::array<const Candidate*,6> nearest{};
    for(const auto& candidate:geometry.candidates) {
      auto& p=nearest[candidate.zone];
      if(!p || candidate.point.x<p->point.x) p=&candidate;
    }
    for(int z=0;z<6;++z) {
      auto& t=tracks_[z]; const auto* p=nearest[z];
      if(!p) {t.hits=0; continue;} // Missing evidence never counts as observed clearance.
      if(stamp<=t.observed || stamp-t.observed>250000000 || separation(p->point,t.point)>0.35) t={};
      if(!t.id) t.id=++next_id_;
      ++t.hits;
      t.point=p->point; t.observed=stamp;
      if(p->point.x<=c.caution && (t.hits>=2 || (p->point.x<=c.urgent && p->support>=c.urgent_support)))
        t.active=true;
      if(t.active) {
        if(p->point.x>c.caution+0.2) {if(++t.clear>=5) t.active=false;}
        else t.clear=0;
        // Urgent severity also has hysteresis; ordinary recovery cannot flap.
        if(p->point.x<=c.urgent) t.urgent=true;
        else if(p->point.x>c.urgent+0.2) t.urgent=false;
      }
    }
  }
  std::vector<Event> events(int64_t now) const {
    std::vector<Event> result;
    for(int z=0;z<6;++z) {
      const auto& t=tracks_[z];
      if(t.active && now>=t.observed && now-t.observed<250000000)
        result.push_back({"obstacle-"+std::to_string(t.id),z,t.urgent,t.point.x,t.observed});
    }
    std::sort(result.begin(),result.end(),[](const Event& a,const Event& b) {
      return a.urgent!=b.urgent?a.urgent>b.urgent:a.distance<b.distance;
    });
    return result;
  }
};
} // namespace hazards
