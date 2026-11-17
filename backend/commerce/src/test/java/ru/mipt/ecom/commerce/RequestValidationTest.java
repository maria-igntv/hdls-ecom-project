package ru.mipt.ecom.commerce;

import jakarta.validation.Validation;
import jakarta.validation.Validator;
import jakarta.validation.ValidatorFactory;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

import static org.assertj.core.api.Assertions.assertThat;

class RequestValidationTest {
    private static ValidatorFactory factory;
    private static Validator validator;

    @BeforeAll
    static void setUp() {
        factory = Validation.buildDefaultValidatorFactory();
        validator = factory.getValidator();
    }

    @AfterAll
    static void close() {
        factory.close();
    }

    @ParameterizedTest
    @CsvSource({"-1,false", "0,true", "1,true", "100,true", "101,false"})
    void quantityAllowsRemovalAndBoundsPurchaseSize(int quantity, boolean valid) {
        assertThat(validator.validate(new CommerceController.Quantity(quantity)).isEmpty()).isEqualTo(valid);
    }

    @ParameterizedTest
    @CsvSource({"-1,0,false", "0,-1,false", "0,0,true", "1,100,true"})
    void checkoutRequiresNonnegativeVersionAndTotal(long version, long total, boolean valid) {
        assertThat(validator.validate(new CommerceController.Checkout(version, total)).isEmpty()).isEqualTo(valid);
    }

    @ParameterizedTest
    @CsvSource({"0,false", "1,true", "100000000000,true", "100000000001,false"})
    void priceRemainsInsideSupportedMoneyRange(long price, boolean valid) {
        var update = new CommerceController.ProductUpdate("Product", price, true);
        assertThat(validator.validate(update).isEmpty()).isEqualTo(valid);
    }
}
