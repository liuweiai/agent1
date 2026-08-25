package com.factorypilot.gateway.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.context.annotation.Configuration;

@Configuration
@ConfigurationProperties(prefix = "factorypilot.security")
public class SecurityProperties {
    private String jwtSecret = "factorypilot-demo-jwt-secret-change-me-2026";
    private long tokenTtlSeconds = 28800;
    private boolean secureCookie = false;

    public String getJwtSecret() { return jwtSecret; }
    public void setJwtSecret(String jwtSecret) { this.jwtSecret = jwtSecret; }
    public long getTokenTtlSeconds() { return tokenTtlSeconds; }
    public void setTokenTtlSeconds(long tokenTtlSeconds) { this.tokenTtlSeconds = tokenTtlSeconds; }
    public boolean isSecureCookie() { return secureCookie; }
    public void setSecureCookie(boolean secureCookie) { this.secureCookie = secureCookie; }
}
