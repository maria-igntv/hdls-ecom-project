package ru.mipt.ecom.commerce;

import org.junit.jupiter.api.Test;
import org.springframework.mock.web.*;
import org.springframework.jdbc.CannotGetJdbcConnectionException;
import org.springframework.security.authentication.*;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

class AvailabilityErrorTest {
    @Test void publicDatabaseOutageIs503() throws Exception {
        var service=mock(CommerceService.class);
        when(service.card(1)).thenThrow(new CannotGetJdbcConnectionException("Database offline"));
        var mvc=MockMvcBuilders.standaloneSetup(new CommerceController(service,mock(ProductCache.class))).setControllerAdvice(new ErrorHandler()).build();
        mvc.perform(get("/api/products/1")).andExpect(status().isServiceUnavailable()).andExpect(jsonPath("$.code").value("DATABASE_UNAVAILABLE"));
    }
    @Test void authBackendOutageIsNotBadCredentials() throws Exception {
        var response=new MockHttpServletResponse();
        SecurityConfig.availabilityEntryPoint().commence(new MockHttpServletRequest(),response,new InternalAuthenticationServiceException("SQL offline",new CannotGetJdbcConnectionException("offline")));
        assertThat(response.getStatus()).isEqualTo(503);
        assertThat(response.getContentAsString()).contains("AUTHENTICATION_UNAVAILABLE");
    }
    @Test void badCredentialsStillRequireAuthentication() throws Exception {
        var response=new MockHttpServletResponse();
        SecurityConfig.availabilityEntryPoint().commence(new MockHttpServletRequest(),response,new BadCredentialsException("Invalid password"));
        assertThat(response.getStatus()).isEqualTo(401);
        assertThat(response.getHeader("WWW-Authenticate")).contains("Basic");
    }
}
