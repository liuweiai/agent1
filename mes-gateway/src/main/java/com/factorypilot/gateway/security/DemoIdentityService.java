package com.factorypilot.gateway.security;

import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;

import java.util.*;

@Service
public class DemoIdentityService {
    private final Map<String, UserIdentity> users = new HashMap<>();
    private final PasswordEncoder passwordEncoder;

    public DemoIdentityService(PasswordEncoder passwordEncoder) {
        this.passwordEncoder = passwordEncoder;
        add("operator", "operator123", "产线操作员", Collections.singletonList("OPERATOR"));
        add("supervisor", "supervisor123", "生产主管-刘工", Arrays.asList("OPERATOR", "SUPERVISOR"));
        add("admin", "admin123", "系统管理员", Arrays.asList("OPERATOR", "SUPERVISOR", "ADMIN"));
    }

    private void add(String username, String password, String displayName, List<String> roles) {
        users.put(username, new UserIdentity(username, passwordEncoder.encode(password), displayName, roles));
    }

    public Optional<UserIdentity> authenticate(String username, String password) {
        UserIdentity user = users.get(username);
        return user != null && passwordEncoder.matches(password, user.getPasswordHash())
                ? Optional.of(user) : Optional.empty();
    }

    public static class UserIdentity {
        private final String username;
        private final String passwordHash;
        private final String displayName;
        private final List<String> roles;

        public UserIdentity(String username, String passwordHash, String displayName, List<String> roles) {
            this.username = username;
            this.passwordHash = passwordHash;
            this.displayName = displayName;
            this.roles = Collections.unmodifiableList(new ArrayList<>(roles));
        }
        public String getUsername() { return username; }
        public String getPasswordHash() { return passwordHash; }
        public String getDisplayName() { return displayName; }
        public List<String> getRoles() { return roles; }
    }
}
