import requests
import json
import time
import sys

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION - UPDATE THESE TO MATCH YOUR ENVIRONMENT
# ═══════════════════════════════════════════════════════════════════════════
KEYCLOAK_URL = "http://localhost:8080"
REALM = "myrealm"
CLIENT_ID = "bank_chat"
CLIENT_SECRET = None  # Set to string if client is confidential

API_BASE_URL = "http://localhost:8000/api/v1/chatbot"  # Your Django backend URL

ADMIN_USERNAME = "chayma"
ADMIN_PASSWORD = "admin"
# ═══════════════════════════════════════════════════════════════════════════

# Terminal Colors
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
RESET = '\033[0m'
BOLD = '\033[1m'

class ApiTester:
    def __init__(self):
        self.token = None
        self.test_role_name = f"pytest-temp-{int(time.time())}"
        self.test_user_id = None
        self.passed = 0
        self.failed = 0
        self.skipped = 0

    def log(self, msg, color=RESET):
        print(f"{color}{msg}{RESET}")

    def assert_status(self, response, expected_status, test_name):
        """Helper to check status and print results."""
        if response.status_code == expected_status:
            self.passed += 1
            self.log(f"   ✅ {test_name} (Status: {response.status_code})", GREEN)
            return True
        else:
            self.failed += 1
            self.log(f"   ❌ {test_name}", RED)
            self.log(f"      Expected: {expected_status}, Got: {response.status_code}", RED)
            try:
                error_details = response.json()
                self.log(f"      Response: {json.dumps(error_details, indent=4)}", RED)
            except Exception:
                self.log(f"      Response: {response.text}", RED)
            return False

    def get_token(self):
        """Authenticate with Keycloak and store the token."""
        self.log("\n🔑 STEP 1: Authenticating with Keycloak...", CYAN)
        url = f"{KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/token"
        data = {
            "grant_type": "password",
            "client_id": CLIENT_ID,
            "username": ADMIN_USERNAME,
            "password": ADMIN_PASSWORD,
        }
        if CLIENT_SECRET:
            data["client_secret"] = CLIENT_SECRET

        resp = requests.post(url, data=data)
        if resp.status_code == 200:
            self.token = resp.json().get("access_token")
            self.log("   Successfully authenticated.", GREEN)
            return True
        else:
            self.log("   Failed to get token!", RED)
            self.log(f"   {resp.text}", RED)
            return False

    def get_headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

    def setup_test_data(self):
        """Fetch a real user ID from the system to test user-specific endpoints."""
        self.log("\n📦 STEP 2: Fetching test data (User ID)...", CYAN)
        resp = requests.get(f"{API_BASE_URL}/users/", headers=self.get_headers())
        
        if resp.status_code == 200 and len(resp.json()) > 0:
            # Pick the first user (hopefully the admin)
            self.test_user_id = resp.json()[0]['id']
            self.log(f"   Using User ID: {self.test_user_id}", GREEN)
            return True
        else:
            self.log("   Could not fetch users. User-specific tests will be skipped.", YELLOW)
            return False

    def teardown(self):
        """Remove the temporary role created during tests."""
        self.log("\n🧹 CLEANUP: Deleting temporary test role...", CYAN)
        if self.token:
            requests.delete(
                f"{API_BASE_URL}/roles/{self.test_role_name}/",
                headers=self.get_headers()
            )
            self.log(f"   Deleted '{self.test_role_name}'", GREEN)

    # ═══════════════════════════════════════════════════════════════════════
    # TEST METHODS
    # ═══════════════════════════════════════════════════════════════════════

    def test_current_user(self):
        self.log("\n👤 Testing: Current User Endpoints", BOLD)
        resp = requests.get(f"{API_BASE_URL}/me/", headers=self.get_headers())
        self.assert_status(resp, 200, "GET /me/ returns 200")
        
        if resp.status_code == 200:
            data = resp.json()
            assert 'roles' in data, "Token missing roles!"
            self.log(f"      Username: {data.get('username')}, Roles: {data.get('roles')}", YELLOW)

    def test_role_crud(self):
        self.log("\n🔐 Testing: Role CRUD Endpoints", BOLD)
        
        # 1. List Roles
        resp = requests.get(f"{API_BASE_URL}/roles/", headers=self.get_headers())
        success = self.assert_status(resp, 200, "GET /roles/ lists roles")
        
        # 2. Create Role
        resp = requests.post(f"{API_BASE_URL}/roles/", headers=self.get_headers(), json={
            "name": self.test_role_name,
            "description": "Temporary role for API testing"
        })
        success = self.assert_status(resp, 201, f"POST /roles/ creates '{self.test_role_name}'")
        if not success:
            self.log("      Skipping role detail tests (creation failed).", YELLOW)
            return # Stop testing this block if creation fails

        # 3. Get Specific Role
        resp = requests.get(f"{API_BASE_URL}/roles/{self.test_role_name}/", headers=self.get_headers())
        self.assert_status(resp, 200, f"GET /roles/{self.test_role_name}/ returns role")
        if resp.status_code == 200:
            assert resp.json()['name'] == self.test_role_name

        # 4. Update Role
        resp = requests.put(f"{API_BASE_URL}/roles/{self.test_role_name}/", headers=self.get_headers(), json={
            "description": "Updated description"
        })
        self.assert_status(resp, 200, f"PUT /roles/{self.test_role_name}/ updates role")

        # 5. Get Users with Role
        resp = requests.get(f"{API_BASE_URL}/roles/{self.test_role_name}/users/", headers=self.get_headers())
        self.assert_status(resp, 200, f"GET /roles/{self.test_role_name}/users/ returns list")

        # 6. Delete Role
        resp = requests.delete(f"{API_BASE_URL}/roles/{self.test_role_name}/", headers=self.get_headers())
        self.assert_status(resp, 200, f"DELETE /roles/{self.test_role_name}/ deletes role")

    def test_user_management(self):
        self.log("\n👥 Testing: User Management Endpoints", BOLD)
        
        if not self.test_user_id:
            self.log("   ⚠️ Skipped (No user ID available)", YELLOW)
            self.skipped += 4
            return

        # 1. List Users
        resp = requests.get(f"{API_BASE_URL}/users/", headers=self.get_headers())
        self.assert_status(resp, 200, "GET /users/ lists users")

        # 2. Search Users
        resp = requests.get(f"{API_BASE_URL}/users/?max=1", headers=self.get_headers())
        self.assert_status(resp, 200, "GET /users/?max=1 pagination works")

        # 3. Get User Detail
        resp = requests.get(f"{API_BASE_URL}/users/{self.test_user_id}/", headers=self.get_headers())
        success = self.assert_status(resp, 200, f"GET /users/{{id}}/ gets user details")
        if resp.status_code == 200:
            assert 'roles' in resp.json(), "User details missing roles array!"

    def test_role_assignment(self):
        self.log("\n🔄 Testing: User Role Assignment Endpoints", BOLD)
        
        if not self.test_user_id:
            self.log("   ⚠️ Skipped (No user ID available)", YELLOW)
            self.skipped += 4
            return

        # Pre-requisite: Ensure the test role exists
        requests.post(f"{API_BASE_URL}/roles/", headers=self.get_headers(), json={
            "name": self.test_role_name
        })

        # 1. Get User Roles
        resp = requests.get(f"{API_BASE_URL}/users/{self.test_user_id}/roles/", headers=self.get_headers())
        self.assert_status(resp, 200, f"GET /users/{{id}}/roles/ fetches user roles")

        # 2. Assign Role
        resp = requests.post(f"{API_BASE_URL}/users/{self.test_user_id}/roles/", headers=self.get_headers(), json={
            "roles": [self.test_role_name]
        })
        success = self.assert_status(resp, 200, f"POST /users/{{id}}/roles/ assigns '{self.test_role_name}'")
        
        # Verify assignment worked
        if success:
            resp = requests.get(f"{API_BASE_URL}/users/{self.test_user_id}/", headers=self.get_headers())
            if resp.status_code == 200:
                roles = resp.json().get('roles', [])
                if self.test_role_name in roles:
                    self.log(f"      ✅ Verification: Role '{self.test_role_name}' is in user's role list", GREEN)
                else:
                    self.log(f"      ❌ Verification: Role '{self.test_role_name}' NOT in user's role list!", RED)
                    self.failed += 1

        # 3. Remove Role
        resp = requests.delete(f"{API_BASE_URL}/users/{self.test_user_id}/roles/", headers=self.get_headers(), json={
            "roles": [self.test_role_name]
        })
        self.assert_status(resp, 200, f"DELETE /users/{{id}}/roles/ removes '{self.test_role_name}'")

        # Cleanup temp role again (will be called again in teardown, but safe)
        requests.delete(f"{API_BASE_URL}/roles/{self.test_role_name}/", headers=self.get_headers())

    def test_permission_denied(self):
        self.log("\n🚫 Testing: Permission Boundaries", BOLD)
        
        # Test without token
        resp = requests.get(f"{API_BASE_URL}/roles/")
        self.assert_status(resp, 401, "GET /roles/ without token returns 401 Unauthorized")

        # Note: Testing 403 Forbidden would require getting a token for a non-admin user.
        # You can add that manually if you have a test user without admin rights.

    # ═══════════════════════════════════════════════════════════════════════
    # RUNNER
    # ═══════════════════════════════════════════════════════════════════════

    def run(self):
        self.log("\n" + "="*60, BOLD)
        self.log("       DJANGO ROLE MANAGEMENT API TEST SUITE", BOLD)
        self.log("="*60, BOLD)

        # 1. Auth
        if not self.get_token():
            sys.exit(1)

        # 2. Setup
        self.setup_test_data()

        # 3. Execute Tests
        try:
            self.test_current_user()
            self.test_role_crud()
            self.test_user_management()
            self.test_role_assignment()
            self.test_permission_denied()
        except Exception as e:
            self.log(f"\n💥 FATAL ERROR: {e}", RED)
            import traceback
            traceback.print_exc()
        finally:
            # 4. Cleanup
            self.teardown()

        # 5. Summary
        self.log("\n" + "="*60, BOLD)
        total = self.passed + self.failed + self.skipped
        self.log(f"       RESULTS: {self.passed}/{total} Passed", GREEN if self.failed == 0 else RED)
        if self.failed > 0:
            self.log(f"       FAILED:  {self.failed}", RED)
        if self.skipped > 0:
            self.log(f"       SKIPPED: {self.skipped}", YELLOW)
        self.log("="*60 + "\n", BOLD)
        
        if self.failed > 0:
            sys.exit(1)

if __name__ == "__main__":
    tester = ApiTester()
    tester.run()