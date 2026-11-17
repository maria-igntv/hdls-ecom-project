package ru.mipt.ecom.commerce;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.boot.availability.ApplicationAvailability;
import org.springframework.boot.availability.ReadinessState;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.jdbc.core.JdbcTemplate;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

class ProbeControllerTest {
    private ApplicationAvailability availability;
    private JdbcTemplate db;
    private ProbeController controller;

    @BeforeEach
    void setUp() {
        availability = mock(ApplicationAvailability.class);
        db = mock(JdbcTemplate.class);
        controller = new ProbeController(availability, db);
    }

    @Test
    void startupAndDrainingRefuseTrafficBeforeAccessingSql() {
        when(availability.getReadinessState()).thenReturn(ReadinessState.REFUSING_TRAFFIC);
        assertThat(controller.ready().getStatusCode().value()).isEqualTo(503);
        verifyNoInteractions(db);
    }

    @Test
    void acceptingTrafficAlsoRequiresWorkingSql() {
        when(availability.getReadinessState()).thenReturn(ReadinessState.ACCEPTING_TRAFFIC);
        when(db.queryForObject("SELECT 1", Integer.class)).thenThrow(new DataAccessResourceFailureException("unavailable"));
        assertThat(controller.ready().getStatusCode().value()).isEqualTo(503);
    }

    @Test
    void warmedApplicationAndWorkingSqlAreReady() {
        when(availability.getReadinessState()).thenReturn(ReadinessState.ACCEPTING_TRAFFIC);
        when(db.queryForObject("SELECT 1", Integer.class)).thenReturn(1);
        assertThat(controller.ready().getStatusCode().value()).isEqualTo(200);
    }

    @Test
    void livenessRemainsAvailableDuringSqlFailure() {
        assertThat(controller.live()).isEqualTo(java.util.Map.of("status", "UP"));
        verifyNoInteractions(db);
    }
}
