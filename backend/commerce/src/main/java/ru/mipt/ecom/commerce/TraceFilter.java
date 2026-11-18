package ru.mipt.ecom.commerce;

import jakarta.servlet.*;
import jakarta.servlet.http.*;
import java.io.IOException;
import java.util.UUID;
import org.slf4j.MDC;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

@Component
@Profile("!worker")
class TraceFilter extends OncePerRequestFilter {
    @Override protected void doFilterInternal(HttpServletRequest request,HttpServletResponse response,FilterChain chain) throws ServletException,IOException {
        UUID trace;
        try { trace=UUID.fromString(request.getHeader("X-Request-ID")); } catch(Exception e) { trace=UUID.randomUUID(); }
        MDC.put("trace_id",trace.toString()); response.setHeader("X-Request-ID",trace.toString());
        try { chain.doFilter(request,response); } finally { MDC.remove("trace_id"); }
    }
}
