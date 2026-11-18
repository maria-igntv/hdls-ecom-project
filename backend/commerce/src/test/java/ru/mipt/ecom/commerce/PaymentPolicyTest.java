package ru.mipt.ecom.commerce;

import java.nio.charset.StandardCharsets;
import java.util.HexFormat;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import static org.assertj.core.api.Assertions.*;

class PaymentPolicyTest {
    @ParameterizedTest
    @CsvSource({
        "PENDING,PENDING,true,PENDING", "PENDING,SUCCEEDED,true,SUCCEEDED", "PENDING,SUCCEEDED,false,REFUND_PENDING",
        "PENDING,FAILED,true,FAILED", "REFUND_PENDING,SUCCEEDED,false,REFUND_PENDING", "REFUND_PENDING,REFUNDED,false,REFUNDED",
        "SUCCEEDED,PENDING,true,SUCCEEDED", "SUCCEEDED,SUCCEEDED,false,SUCCEEDED", "FAILED,PENDING,true,FAILED", "REFUNDED,SUCCEEDED,false,REFUNDED"
    })
    void monotonicPaymentPolicy(String local,String remote,boolean live,String expected) {
        assertThat(PaymentService.transition(local,remote,live)).isEqualTo(expected);
    }
    @Test void webhookAuthenticatesExactRawBytes() throws Exception {
        byte[] body="{\"a\":1}".getBytes(StandardCharsets.UTF_8);
        var mac=Mac.getInstance("HmacSHA256"); mac.init(new SecretKeySpec("secret".getBytes(StandardCharsets.UTF_8),"HmacSHA256"));
        mac.update("123.".getBytes(StandardCharsets.UTF_8));
        String signature=HexFormat.of().formatHex(mac.doFinal(body));
        assertThat(PaymentController.verify("secret",123,body,signature)).isTrue();
        assertThat(PaymentController.verify("secret",124,body,signature)).isFalse();
        assertThat(PaymentController.verify("secret",123,"{}".getBytes(StandardCharsets.UTF_8),signature)).isFalse();
        assertThat(PaymentController.verify("secret",123,body,"invalid")).isFalse();
    }
    @Test void mediaRejectsExecutableClaimingToBePng() {
        assertThatThrownBy(()->MediaService.extension("<script>".getBytes(StandardCharsets.UTF_8),"image/png")).isInstanceOf(CommerceService.ApiFailure.class);
    }
}
