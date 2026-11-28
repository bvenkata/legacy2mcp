package com.shop.controller;

import java.util.List;
import org.springframework.web.bind.annotation.*;
import org.springframework.http.ResponseEntity;
import com.shop.service.PaymentService;
import com.shop.repository.OrderRepository;

@RestController
@RequestMapping("/api/orders")
public class OrderController {

    private final OrderRepository orderRepository;
    private final PaymentService paymentService;

    public OrderController(OrderRepository orderRepository, PaymentService paymentService) {
        this.orderRepository = orderRepository;
        this.paymentService = paymentService;
    }

    @GetMapping
    public List<Order> listOrders(@RequestParam(name = "status", required = false) String status) {
        return orderRepository.findAll();
    }

    @GetMapping("/{id}")
    public ResponseEntity<Order> getOrder(@PathVariable("id") String orderId) {
        return orderRepository.findById(orderId)
                .map(ResponseEntity::ok)
                .orElse(ResponseEntity.notFound().build());
    }

    @PostMapping
    public Order placeOrder(@RequestBody Order order) {
        Order saved = orderRepository.save(order);
        paymentService.chargeCard(saved);
        return saved;
    }
}
