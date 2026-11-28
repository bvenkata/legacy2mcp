package com.shop.service;

import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;
import com.stripe.model.Charge;

@Service
public class PaymentService {

    private final RestTemplate restTemplate;

    public PaymentService(RestTemplate restTemplate) {
        this.restTemplate = restTemplate;
    }

    public void chargeCard(Object order) {
        restTemplate.postForObject("https://payment.example.com/charge", order, String.class);
        Charge.create(null);
    }
}
