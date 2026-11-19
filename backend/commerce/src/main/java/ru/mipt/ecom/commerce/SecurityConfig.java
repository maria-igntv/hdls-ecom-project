package ru.mipt.ecom.commerce;

import org.springframework.context.annotation.Profile;

import java.util.List;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpMethod;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.config.Customizer;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.userdetails.User;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.core.userdetails.UsernameNotFoundException;
import org.springframework.security.web.SecurityFilterChain;

@Configuration
@Profile("!worker")
class SecurityConfig {
    static org.springframework.security.web.AuthenticationEntryPoint availabilityEntryPoint() {
        return (request,response,error)-> {
            boolean unavailable=error instanceof org.springframework.security.authentication.AuthenticationServiceException;
            response.setStatus(unavailable?503:401);
            response.setContentType("application/json");
            if(!unavailable) response.setHeader("WWW-Authenticate","Basic realm=\"ecom\"");
            response.getWriter().write(unavailable?"{\"code\":\"AUTHENTICATION_UNAVAILABLE\"}":"{\"code\":\"AUTHENTICATION_REQUIRED\"}");
        };
    }
    static final class StoreUser extends User {
        final long id;
        StoreUser(long id,String username,String password,String role) {
            super(username,password,List.of(new SimpleGrantedAuthority("ROLE_"+role)));
            this.id=id;
        }
    }
    @Bean UserDetailsService users(JdbcTemplate db) {
        return username -> db.query("SELECT id,username,password_hash,role FROM users WHERE username=?",
            (rs,n) -> new StoreUser(rs.getLong(1),rs.getString(2),rs.getString(3),rs.getString(4)),username)
            .stream().findFirst().orElseThrow(() -> new UsernameNotFoundException("Unknown account"));
    }
    @Bean SecurityFilterChain security(HttpSecurity http) throws Exception {
        return http.csrf(csrf -> csrf.disable()).sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
            .authorizeHttpRequests(auth -> auth
                .dispatcherTypeMatchers(jakarta.servlet.DispatcherType.ERROR).permitAll()
                .requestMatchers(r -> r.getLocalPort()==9091 && (r.getRequestURI().equals("/actuator/prometheus") || r.getRequestURI().startsWith("/actuator/health"))).permitAll()
                .requestMatchers("/api/payments/webhook").permitAll()
                .requestMatchers("/readyz","/livez","/actuator/health/**").permitAll()
                .requestMatchers(HttpMethod.GET,"/api/products","/api/products/*").permitAll()
                .requestMatchers("/api/admin/**","/actuator/**").hasRole("ADMIN")
                .requestMatchers("/api/cart/**","/api/orders/**").hasRole("BUYER")
                .anyRequest().authenticated())
            .httpBasic(b -> b.authenticationEntryPoint(availabilityEntryPoint())).build();
    }
}
