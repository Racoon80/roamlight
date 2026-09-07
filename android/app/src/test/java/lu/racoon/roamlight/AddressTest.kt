//  Deen eenzege Sécherheets-Check, deen d'App selwer mécht -- also gëtt en och
//  gepréift an net just behaapt.

package lu.racoon.roamlight

import org.junit.Assert.assertThrows
import org.junit.Test

class AddressTest {

    private fun ok(url: String) = Api.requireSafeAddress(url)

    @Test
    fun `https is always fine`() {
        ok("https://photos.example.com")
        ok("https://192.0.2.10:8443")
        ok("https://photos.example.com:8080/")
    }

    @Test
    fun `http is fine on the home network`() {
        ok("http://192.168.1.62:8080")
        ok("http://10.0.0.5")
        ok("http://172.16.4.4")
        ok("http://172.31.255.1")
        ok("http://127.0.0.1:8080")
        ok("http://localhost:8080")
        ok("http://nas.local")
        ok("http://beast")                 // a bare name is a machine next door
    }

    @Test
    fun `http to the internet is refused`() {
        for (bad in listOf(
            "http://photos.example.com",
            "http://203.0.113.9",
            "http://172.32.0.1",           // just outside 172.16/12
            "http://8.8.8.8:8080",
            "http://evil.example.org/app/pair",
        )) {
            assertThrows("should have been refused: $bad", ApiError::class.java) { ok(bad) }
        }
    }

    @Test
    fun `nonsense is refused`() {
        assertThrows(ApiError::class.java) { ok("not an address at all") }
        assertThrows(ApiError::class.java) { ok("") }
    }
}
