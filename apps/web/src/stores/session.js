import { computed, reactive } from "vue";
import { coreApi } from "@/api/core";
import { setCsrf } from "./auth-token";
const state = reactive({ user: null, ready: false });
let inflight = null;
function apply(data) {
  state.user = data?.user ?? null;
  setCsrf(data?.csrf_token);
  state.ready = true;
}
export const session = {
  state,
  isLoggedIn: computed(() => Boolean(state.user)),
  isAdmin: computed(() => state.user?.role === "superuser"),
  async loadMe(force = false) {
    if (state.ready && !force) return state.user;
    if (!inflight)
      inflight = coreApi
        .me()
        .then(apply)
        .catch((error) => {
          apply(null);
          if (error.status !== 401 && error.status !== 404) throw error;
        })
        .finally(() => {
          inflight = null;
        });
    await inflight;
    return state.user;
  },
  async login(token) {
    apply(await coreApi.exchange(token));
    return state.user;
  },
  async logout() {
    try {
      await coreApi.logout();
    } finally {
      apply(null);
    }
  },
};
