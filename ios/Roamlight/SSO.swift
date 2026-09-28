//  Signing in through the family's identity provider (single sign-on).
//
//  ⚠ The app is NOT a second client at the provider. It opens the site's own
//    sign-in (`/auth/oidc/login`) in the system's browser sheet, the provider
//    sends the person back to the site exactly as it does for the website, and
//    the site ends on `/app/sso`: "connect this phone?". Its answer is a
//    pairing code that comes back through `roamlight://sso?c=…`. Nothing had to
//    change at the provider -- the redirect address it knows stays the only one.
//
//  ⚠ The code is bound to a secret that never leaves this app (PKCE, S256):
//    the site stores only its hash with the code, and trades the code for a
//    token only against the secret itself. Whoever catches the address holds
//    nothing.

import AuthenticationServices
import CryptoKit
import UIKit

@MainActor
final class SSO: NSObject, ASWebAuthenticationPresentationContextProviding {
    static let shared = SSO()
    private var session: ASWebAuthenticationSession?

    enum Failure: LocalizedError {
        case noSSO, cancelled, noCode
        var errorDescription: String? {
            switch self {
            case .noSSO: return "This site has no single sign-on. Use the code or a password."
            case .cancelled: return "Sign-in cancelled."
            case .noCode: return "The site did not send a code back."
            }
        }
    }

    /// Run the whole thing. Returns the pairing code and the secret it is bound to.
    func run(site: String) async throws -> (code: String, verifier: String) {
        guard try await API.ways(site: site).sso else { throw Failure.noSSO }

        var raw = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, raw.count, &raw) == errSecSuccess else {
            throw Failure.noCode
        }
        let verifier = Self.b64url(Data(raw))
        let challenge = Self.b64url(Data(SHA256.hash(data: Data(verifier.utf8))))

        var comps = URLComponents(url: Site.url.appendingPathComponent("auth/oidc/login"),
                                  resolvingAgainstBaseURL: false)!
        comps.queryItems = [URLQueryItem(name: "next", value: "/app/sso?challenge=\(challenge)")]
        guard let start = comps.url else { throw Failure.noCode }

        let back: URL = try await withCheckedThrowingContinuation { cont in
            let s = ASWebAuthenticationSession(url: start, callbackURLScheme: "roamlight") { url, err in
                if let url { cont.resume(returning: url) }
                else if let e = err as? ASWebAuthenticationSessionError, e.code == .canceledLogin {
                    cont.resume(throwing: Failure.cancelled)
                } else { cont.resume(throwing: err ?? Failure.noCode) }
            }
            s.presentationContextProvider = self
            // ⚠ NOT ephemeral: somebody already signed in at the provider in
            //   Safari should not have to type their password again. That is
            //   the whole "single" in single sign-on.
            s.prefersEphemeralWebBrowserSession = false
            session = s
            s.start()
        }
        session = nil
        guard back.host == "sso",
              let code = URLComponents(url: back, resolvingAgainstBaseURL: false)?
                .queryItems?.first(where: { $0.name == "c" })?.value,
              !code.isEmpty else { throw Failure.noCode }
        return (code, verifier)
    }

    nonisolated func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        MainActor.assumeIsolated {
            UIApplication.shared.connectedScenes
                .compactMap { ($0 as? UIWindowScene)?.keyWindow }.first ?? ASPresentationAnchor()
        }
    }

    private static func b64url(_ d: Data) -> String {
        d.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}
