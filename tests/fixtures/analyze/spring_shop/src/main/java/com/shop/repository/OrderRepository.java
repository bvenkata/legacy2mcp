package com.shop.repository;

import java.util.List;
import java.util.Optional;
import org.springframework.stereotype.Repository;

@Repository
public class OrderRepository {

    public List<Object> findAll() {
        return List.of();
    }

    public Optional<Object> findById(String id) {
        return Optional.empty();
    }

    public Object save(Object order) {
        return order;
    }
}
