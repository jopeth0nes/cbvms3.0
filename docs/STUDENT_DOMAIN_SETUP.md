# studentportal.bond on this PC

The desktop app and website share `data/cbvms.db`. The PC must remain on,
connected to the Internet, with the portal and Cloudflare Tunnel running.
The domain is not live until the account, DNS, and tunnel steps below are complete.

## Domain setup (account owner)

1. Create an account at https://dash.cloudflare.com/sign-up.
2. Add the existing domain `studentportal.bond` and select the Free plan.
3. Review the imported DNS records and retain any existing email or other service records.
4. Copy the two nameservers assigned to this domain by Cloudflare.
5. In Hostinger hPanel, open Domains, manage `studentportal.bond`, and open
   DNS / Nameservers. Change the nameservers to the two provided by Cloudflare.
   The registration remains at Hostinger.
6. Wait for Cloudflare to show the domain as Active.

## Start the portal

From the project root in PowerShell:

```powershell
.\scripts\run_student_domain.ps1
```

This binds to `127.0.0.1:8081` with the configured public origin
`https://studentportal.bond`. It checks that exact origin for writes and issues
Secure session cookies. Use the public HTTPS domain for sign-in; direct HTTP
sign-in on port 8081 is intentionally rejected. The existing local-only portal on
port 8080 can continue running separately.

## Connect the tunnel

1. In Cloudflare, go to Networking > Tunnels and create a tunnel called
   `cbvms-student-portal`.
2. Choose Windows / 64-bit and follow the displayed connector instructions
   on this PC. The tunnel client has already been downloaded to
   `data\tools\cloudflared.exe`.
3. Keep tunnel tokens in your local account setup; do not paste tokens into chat
   or commit them to the repository.
4. Add a Published application route with hostname `studentportal.bond`
   (no subdomain), service type HTTP, and URL `127.0.0.1:8081`.
5. Resolve any conflicting apex website record before creating the route;
   retain unrelated DNS records.
6. Once the connector is Healthy, open `https://studentportal.bond` and verify
   student sign-in, dashboard records, and sign-out.

No router port forwarding is needed. The portal binds only to this PC.
Automatic startup after reboot still needs to be configured once the account
and tunnel are connected. Starting only the tunnel service does not start Python.

## References

- [Cloudflare Tunnel setup](https://developers.cloudflare.com/tunnel/get-started/)
- [Hostinger domain setup for Cloudflare](https://www.hostinger.com/support/4741545-how-to-use-cloudflare-in-hostinger/)
