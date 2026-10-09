// sha256ni.h: SHA-256 block function with the x86 SHA extensions (SHA-NI), selected at run time.
// Based on the public-domain intrinsic code by Sean Gulley / Jeffrey Walton (Intel SHA-NI
// whitepaper). The caller keeps the portable implementation as the fallback.
#pragma once

#include <cstdint>
#include <cstddef>

#if defined(__x86_64__) && (defined(__GNUC__) || defined(__clang__))
#include <cpuid.h>
#include <immintrin.h>
#define JP_HAVE_SHANI_CODE 1

namespace shani {

static inline bool available() {
    unsigned a, b, c, d;
    if (!__get_cpuid_count(7, 0, &a, &b, &c, &d)) return false;
    if (!(b & (1u << 29))) return false;  // SHA
    if (!__get_cpuid(1, &a, &b, &c, &d)) return false;
    return (c & (1u << 19)) && (c & (1u << 9));  // SSE4.1, SSSE3
}

// Process `blocks` 64-byte blocks of `data` into state[8].
__attribute__((target("sha,sse4.1,ssse3"))) static void process(uint32_t state[8], const uint8_t* data,
                                                                 size_t blocks) {
    __m128i STATE0, STATE1, MSG, TMP, MSG0, MSG1, MSG2, MSG3, ABEF_SAVE, CDGH_SAVE;
    const __m128i MASK = _mm_set_epi64x(0x0c0d0e0f08090a0bULL, 0x0405060700010203ULL);

    TMP = _mm_loadu_si128((const __m128i*)&state[0]);
    STATE1 = _mm_loadu_si128((const __m128i*)&state[4]);
    TMP = _mm_shuffle_epi32(TMP, 0xB1);
    STATE1 = _mm_shuffle_epi32(STATE1, 0x1B);
    STATE0 = _mm_alignr_epi8(TMP, STATE1, 8);
    STATE1 = _mm_blend_epi16(STATE1, TMP, 0xF0);

    while (blocks--) {
        ABEF_SAVE = STATE0;
        CDGH_SAVE = STATE1;

        MSG = _mm_loadu_si128((const __m128i*)(data + 0));
        MSG0 = _mm_shuffle_epi8(MSG, MASK);
        MSG = _mm_add_epi32(MSG0, _mm_set_epi64x(0xE9B5DBA5B5C0FBCFULL, 0x71374491428A2F98ULL));
        STATE1 = _mm_sha256rnds2_epu32(STATE1, STATE0, MSG);
        MSG = _mm_shuffle_epi32(MSG, 0x0E);
        STATE0 = _mm_sha256rnds2_epu32(STATE0, STATE1, MSG);

        MSG1 = _mm_loadu_si128((const __m128i*)(data + 16));
        MSG1 = _mm_shuffle_epi8(MSG1, MASK);
        MSG = _mm_add_epi32(MSG1, _mm_set_epi64x(0xAB1C5ED5923F82A4ULL, 0x59F111F13956C25BULL));
        STATE1 = _mm_sha256rnds2_epu32(STATE1, STATE0, MSG);
        MSG = _mm_shuffle_epi32(MSG, 0x0E);
        STATE0 = _mm_sha256rnds2_epu32(STATE0, STATE1, MSG);
        MSG0 = _mm_sha256msg1_epu32(MSG0, MSG1);

        MSG2 = _mm_loadu_si128((const __m128i*)(data + 32));
        MSG2 = _mm_shuffle_epi8(MSG2, MASK);
        MSG = _mm_add_epi32(MSG2, _mm_set_epi64x(0x550C7DC3243185BEULL, 0x12835B01D807AA98ULL));
        STATE1 = _mm_sha256rnds2_epu32(STATE1, STATE0, MSG);
        MSG = _mm_shuffle_epi32(MSG, 0x0E);
        STATE0 = _mm_sha256rnds2_epu32(STATE0, STATE1, MSG);
        MSG1 = _mm_sha256msg1_epu32(MSG1, MSG2);

        MSG3 = _mm_loadu_si128((const __m128i*)(data + 48));
        MSG3 = _mm_shuffle_epi8(MSG3, MASK);
        MSG = _mm_add_epi32(MSG3, _mm_set_epi64x(0xC19BF1749BDC06A7ULL, 0x80DEB1FE72BE5D74ULL));
        STATE1 = _mm_sha256rnds2_epu32(STATE1, STATE0, MSG);
        TMP = _mm_alignr_epi8(MSG3, MSG2, 4);
        MSG0 = _mm_add_epi32(MSG0, TMP);
        MSG0 = _mm_sha256msg2_epu32(MSG0, MSG3);
        MSG = _mm_shuffle_epi32(MSG, 0x0E);
        STATE0 = _mm_sha256rnds2_epu32(STATE0, STATE1, MSG);
        MSG2 = _mm_sha256msg1_epu32(MSG2, MSG3);

#define JP_SHA_ROUND(Ma, Mb, Mc, Md, K1, K0)                                   \
    MSG = _mm_add_epi32(Ma, _mm_set_epi64x(K1, K0));                           \
    STATE1 = _mm_sha256rnds2_epu32(STATE1, STATE0, MSG);                       \
    TMP = _mm_alignr_epi8(Ma, Md, 4);                                          \
    Mb = _mm_add_epi32(Mb, TMP);                                               \
    Mb = _mm_sha256msg2_epu32(Mb, Ma);                                         \
    MSG = _mm_shuffle_epi32(MSG, 0x0E);                                        \
    STATE0 = _mm_sha256rnds2_epu32(STATE0, STATE1, MSG);                       \
    Md = _mm_sha256msg1_epu32(Md, Ma);

        // rounds 16-19 .. 48-51 (12 groups with message schedule)
        JP_SHA_ROUND(MSG0, MSG1, MSG2, MSG3, 0x240CA1CC0FC19DC6ULL, 0xEFBE4786E49B69C1ULL)
        JP_SHA_ROUND(MSG1, MSG2, MSG3, MSG0, 0x76F988DA5CB0A9DCULL, 0x4A7484AA2DE92C6FULL)
        JP_SHA_ROUND(MSG2, MSG3, MSG0, MSG1, 0xBF597FC7B00327C8ULL, 0xA831C66D983E5152ULL)
        JP_SHA_ROUND(MSG3, MSG0, MSG1, MSG2, 0x1429296706CA6351ULL, 0xD5A79147C6E00BF3ULL)
        JP_SHA_ROUND(MSG0, MSG1, MSG2, MSG3, 0x53380D134D2C6DFCULL, 0x2E1B213827B70A85ULL)
        JP_SHA_ROUND(MSG1, MSG2, MSG3, MSG0, 0x92722C8581C2C92EULL, 0x766A0ABB650A7354ULL)
        JP_SHA_ROUND(MSG2, MSG3, MSG0, MSG1, 0xC76C51A3C24B8B70ULL, 0xA81A664BA2BFE8A1ULL)
        JP_SHA_ROUND(MSG3, MSG0, MSG1, MSG2, 0x106AA070F40E3585ULL, 0xD6990624D192E819ULL)
        JP_SHA_ROUND(MSG0, MSG1, MSG2, MSG3, 0x34B0BCB52748774CULL, 0x1E376C0819A4C116ULL)
#undef JP_SHA_ROUND

        // rounds 52-55
        MSG = _mm_add_epi32(MSG1, _mm_set_epi64x(0x682E6FF35B9CCA4FULL, 0x4ED8AA4A391C0CB3ULL));
        STATE1 = _mm_sha256rnds2_epu32(STATE1, STATE0, MSG);
        TMP = _mm_alignr_epi8(MSG1, MSG0, 4);
        MSG2 = _mm_add_epi32(MSG2, TMP);
        MSG2 = _mm_sha256msg2_epu32(MSG2, MSG1);
        MSG = _mm_shuffle_epi32(MSG, 0x0E);
        STATE0 = _mm_sha256rnds2_epu32(STATE0, STATE1, MSG);

        // rounds 56-59
        MSG = _mm_add_epi32(MSG2, _mm_set_epi64x(0x8CC7020884C87814ULL, 0x78A5636F748F82EEULL));
        STATE1 = _mm_sha256rnds2_epu32(STATE1, STATE0, MSG);
        TMP = _mm_alignr_epi8(MSG2, MSG1, 4);
        MSG3 = _mm_add_epi32(MSG3, TMP);
        MSG3 = _mm_sha256msg2_epu32(MSG3, MSG2);
        MSG = _mm_shuffle_epi32(MSG, 0x0E);
        STATE0 = _mm_sha256rnds2_epu32(STATE0, STATE1, MSG);

        // rounds 60-63
        MSG = _mm_add_epi32(MSG3, _mm_set_epi64x(0xC67178F2BEF9A3F7ULL, 0xA4506CEB90BEFFFAULL));
        STATE1 = _mm_sha256rnds2_epu32(STATE1, STATE0, MSG);
        MSG = _mm_shuffle_epi32(MSG, 0x0E);
        STATE0 = _mm_sha256rnds2_epu32(STATE0, STATE1, MSG);

        STATE0 = _mm_add_epi32(STATE0, ABEF_SAVE);
        STATE1 = _mm_add_epi32(STATE1, CDGH_SAVE);
        data += 64;
    }

    TMP = _mm_shuffle_epi32(STATE0, 0x1B);
    STATE1 = _mm_shuffle_epi32(STATE1, 0xB1);
    STATE0 = _mm_blend_epi16(TMP, STATE1, 0xF0);
    STATE1 = _mm_alignr_epi8(STATE1, TMP, 8);
    _mm_storeu_si128((__m128i*)&state[0], STATE0);
    _mm_storeu_si128((__m128i*)&state[4], STATE1);
}

}  // namespace shani
#endif
