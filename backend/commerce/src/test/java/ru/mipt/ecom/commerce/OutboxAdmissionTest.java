package ru.mipt.ecom.commerce;

import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class OutboxAdmissionTest {
    @Test void backlogOrAgeRefusesNewWork() {
        for(var row:java.util.List.of(Map.<String,Object>of("pending",100,"age",0),Map.<String,Object>of("pending",1,"age",600))) {
            var db=mock(JdbcTemplate.class);
            when(db.queryForMap(anyString(),anyLong())).thenReturn(row);
            assertThatThrownBy(()->new OutboxAdmission(db,100,600).check()).isInstanceOfSatisfying(CommerceService.ApiFailure.class,e->{assertThat(e.status).isEqualTo(503);assertThat(e.code).isEqualTo("OUTBOX_CAPACITY");});
        }
    }
    @Test void emptyBacklogDoesNotRejectAndProbeIsCached() {
        var db=mock(JdbcTemplate.class);
        when(db.queryForMap(anyString(),anyLong())).thenReturn(Map.of("pending",0,"age",0));
        var admission=new OutboxAdmission(db,100,600);
        admission.check(); admission.check();
        verify(db,times(1)).queryForMap(anyString(),anyLong());
    }
}
