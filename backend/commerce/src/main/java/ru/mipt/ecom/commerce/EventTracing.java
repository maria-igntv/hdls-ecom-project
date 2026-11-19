package ru.mipt.ecom.commerce;

import io.micrometer.tracing.*;
import io.micrometer.tracing.propagation.Propagator;
import java.util.*;
import org.springframework.stereotype.Component;

@Component
class EventTracing {
    final Tracer tracer; final Propagator propagator;
    EventTracing(Tracer tracer,Propagator propagator) { this.tracer=tracer; this.propagator=propagator; }
    Map<String,String> context() {
        Map<String,String> result=new HashMap<>();
        var span=tracer.currentSpan();
        if(span!=null) propagator.inject(span.context(),result,Map::put);
        return result;
    }
    Span consumer(Map<String,Object> headers,String queue) {
        return propagator.extract(headers,(h,key)->Optional.ofNullable(h.get(key)).map(Object::toString).orElse(null)).name("consume."+queue).start();
    }
}
