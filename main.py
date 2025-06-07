import asyncio
import json
import random
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any
import aiohttp
import aiofiles
from eth_account import Account
from eth_account.signers.local import LocalAccount
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import os
from faker import Faker
import re

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


NUM_OF_AI_INTERACTIONS = str(random.randint(8, 15)) # Number of AI interactions to perform - random between 1 and 5
STAKING_AMOUNT = 1  # minimum is 1 KITE token - i discovered this late. but we will randomize it later after earning more KITE tokens


KITE_AI_SUBNET = '0xb132001567650917d6bd695d1fab55db7986e9a5'
BASE_URL = 'https://neo.prod.gokite.ai'
GOKITE_URL = 'https://ozone-point-system.prod.gokite.ai'
RECAPTCHA_SITE_KEY = '6Lc_VwgrAAAAALtx_UtYQnW-cFg8EPDgJ8QVqkaz'
ENCRYPTION_KEY_HEX = '6a1c35292b7c5b769ff47d89a17e7bc4f0adfe1b462981d28e0e9f7ff20b8f8a'
RPC_URL = "https://rpc-testnet.gokite.ai/"
CONTRACT_ADDRESS = "0x948f52524Bdf595b439e7ca78620A8f843612df3"
POSITION_ID = "0x4b6f5b36bb7706150b17e2eecb6e602b1b90b94a4bf355df57466626a5cb897b"


USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64; rv:89.0) Gecko/20100101 Firefox/89.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:88.0) Gecko/20100101 Firefox/88.0',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1 Mobile/15E148 Safari/604.1',
    'Mozilla/5.0 (Linux; Android 11; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.159 Mobile Safari/537.36',
    'Mozilla/5.0 (iPad; CPU OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1',
    'Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/83.0.4103.116 Safari/537.36',
    'Mozilla/5.0 (Linux; Android 10; Pixel 3 XL) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.120 Mobile Safari/537.36'
]

BASE_HEADERS = {
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7',
    'Origin': 'https://testnet.gokite.ai',
    'Referer': 'https://testnet.gokite.ai/',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-site',
    'User-Agent': random.choice(USER_AGENTS),
    'Content-Type': 'application/json'
}

# Semaphore for limiting concurrent requests
REQUEST_SEMAPHORE = asyncio.Semaphore(50)
WALLET_SEMAPHORE = asyncio.Semaphore(20000)

# Agents configuration
AGENTS = [
    {'name': 'Professor', 'service_id': 'deployment_KiMLvUiTydioiHm7PWZ12zJU'},
    {'name': 'Crypto Buddy', 'service_id': 'deployment_ByVHjMD6eDb9AdekRIbyuz14'},
    {'name': 'Sherlock', 'service_id': 'deployment_OX7sn2D0WvxGUGK8CTqsU5VJ'}
]

async def load_private_keys(file_path: str = 'privatekeys.txt') -> List[Dict[str, Optional[str]]]:
    try:
        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
            content = await f.read()
        lines = [line.strip() for line in content.split('\n') if line.strip()]
        wallets = []
        for line in lines:
            parts = line.split(',')
            if len(parts) >= 1:
                wallet = {
                    'private_key': parts[0].strip(),
                    'neo_session': parts[1].strip() if len(parts) > 1 and parts[1].strip() else None,
                    'refresh_token': parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
                }
                wallets.append(wallet)
            else:
                logger.warning(f"Skipping invalid line in {file_path}: {line}")
        if not wallets:
            logger.error(f"No valid private keys found in {file_path}")
            raise ValueError("No valid private keys found")
        return wallets
    except FileNotFoundError:
        logger.error(f"Private keys file not found: {file_path}")
        raise
    except Exception as e:
        logger.error(f"Failed to load private keys from {file_path}: {e}")
        raise

async def load_prompts(file_path: str = 'prompts.txt') -> Dict[str, List[str]]:
    try:
        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
            content = await f.read()
        lines = [line.strip() for line in content.split('\n')]
        prompt_map = {agent['name']: [] for agent in AGENTS}
        current_agent = None

        for line in lines:
            if line.startswith('[') and line.endswith(']'):
                current_agent = line[1:-1].strip()
                if current_agent not in prompt_map:
                    prompt_map[current_agent] = []
            elif line and not line.startswith('#') and current_agent:
                prompt_map[current_agent].append(line)

        for agent in AGENTS:
            if not prompt_map.get(agent['name']):
                logger.error(f"No prompts found for agent {agent['name']} in {file_path}")
                raise ValueError(f"Missing prompts for {agent['name']}")
        return prompt_map
    except FileNotFoundError:
        logger.error(f"Prompts file not found: {file_path}")
        raise
    except Exception as e:
        logger.error(f"Failed to load prompts from {file_path}: {e}")
        raise

def get_random_prompt(agent_name: str, prompts: Dict[str, List[str]]) -> str:
    agent_prompts = prompts.get(agent_name, [])
    if not agent_prompts:
        logger.warning(f"No prompts available for {agent_name}")
        return ""
    return random.choice(agent_prompts)

def create_wallet(private_key: str) -> Optional[LocalAccount]:
    try:
        Account.enable_unaudited_hdwallet_features()
        wallet = Account.from_key(private_key)
        logger.info(f"Wallet created: {wallet.address}")
        return wallet
    except Exception as e:
        logger.error(f"Invalid private key: {e}")
        return None

def encrypt_address(address: str) -> Optional[str]:
    try:
        key = bytes.fromhex(ENCRYPTION_KEY_HEX)
        aesgcm = AESGCM(key)
        iv = os.urandom(12)
        data = address.encode('utf-8')
        ciphertext = aesgcm.encrypt(iv, data, None)
        return (iv + ciphertext).hex()
    except Exception as e:
        logger.error(f"Failed to encrypt address {address}: {e}")
        return None

def extract_cookies(headers: Dict[str, Any]) -> Optional[str]:
    try:
        raw_cookies = headers.get('set-cookie', [])
        skip_keys = {'expires', 'path', 'domain', 'samesite', 'secure', 'httponly', 'max-age'}
        cookies = []
        for cookie_str in raw_cookies:
            for part in cookie_str.split(';'):
                part = part.strip()
                if '=' in part:
                    key, value = part.split('=', 1)
                    if key.lower() not in skip_keys:
                        cookies.append(f"{key}={value}")
        return '; '.join(cookies) if cookies else None
    except Exception as e:
        logger.error(f"Failed to extract cookies: {e}")
        return None

async def make_eth_call(wallet_address: str) -> Optional[str]:
    for attempt in range(1, 3):
        try:
            function_selector = "0x8cb84e18"
            address_padded = wallet_address[2:].lower().rjust(64, '0')
            position_id_padded = POSITION_ID[2:].rjust(64, '0')
            data = f"{function_selector}{address_padded}{position_id_padded}"

            rpc_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_call",
                "params": [
                    {
                        "to": CONTRACT_ADDRESS,
                        "data": data
                    },
                    "latest"
                ]
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(RPC_URL, json=rpc_payload, headers=BASE_HEADERS) as response:
                    response_text = await response.text()

                    if response.status != 200:
                        logger.error(f"eth_call failed with HTTP status {response.status}")
                        if attempt == 3:
                            return None
                        await asyncio.sleep(2)
                        continue

                    response_data = await response.json()
                    if 'error' in response_data:
                        logger.error(f"eth_call error: {response_data['error']}")
                        if attempt == 3:
                            return None
                        await asyncio.sleep(2)
                        continue

                    result = response_data.get('result')
                    if not result:
                        logger.error("No result in eth_call response")
                        if attempt == 3:
                            return None
                        await asyncio.sleep(2)
                        continue

                    if len(result) < 42:
                        logger.error(f"Invalid eth_call result: {result}")
                        if attempt == 3:
                            return None
                        await asyncio.sleep(2)
                        continue

                    aa_address = "0x" + result[-40:]
                    logger.info(f"Retrieved smart account address: {aa_address}")
                    return aa_address

        except Exception as e:
            logger.error(f"Error during eth_call attempt {attempt}: {e}")
            if attempt == 3:
                return None
            await asyncio.sleep(2)
    return None

async def authenticate_wallet(wallet: LocalAccount, neo_session: Optional[str] = None, refresh_token: Optional[str] = None, max_retries: int = 3) -> Optional[Dict[str, Any]]:
    url = f"{BASE_URL}/v2/signin"
    async with REQUEST_SEMAPHORE:
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"Attempting login for {wallet.address} (Attempt {attempt}/{max_retries})")
                
                auth_token = encrypt_address(wallet.address)
                if not auth_token:
                    logger.error(f"Authentication token generation failed for {wallet.address}")
                    return None
                
                headers = BASE_HEADERS.copy()
                headers['User-Agent'] = random.choice(USER_AGENTS)
                headers['Authorization'] = auth_token
                if neo_session or refresh_token:
                    cookies = []
                    if neo_session:
                        cookies.append(f"neo_session={neo_session}")
                    if refresh_token:
                        cookies.append(f"refresh_token={refresh_token}")
                    headers['Cookie'] = '; '.join(cookies)
                
                payload = {'eoa': wallet.address}
                
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=payload, headers=headers) as resp:
                        if resp.status == 422:
                            logger.info(f"Received 422 status for {wallet.address}, likely new user. Fetching smart account address.")
                            aa_address = await make_eth_call(wallet.address)
                            if not aa_address:
                                logger.error(f"Failed to retrieve smart account address for {wallet.address}")
                                if attempt == max_retries:
                                    return None
                                await asyncio.sleep(2)
                                continue
                            
                            # Retry login with eoa and aa_address 
                            payload = {'eoa': wallet.address, 'aa_address': aa_address}
                            async with session.post(url, json=payload, headers=headers) as retry_resp:
                                if retry_resp.status != 200:
                                    logger.error(f"Retry login failed for {wallet.address}: HTTP {retry_resp.status}")
                                    if attempt == max_retries:
                                        return None
                                    await asyncio.sleep(2)
                                    continue
                                
                                data = await retry_resp.json()
                                if data.get('error'):
                                    logger.error(f"Retry login failed for {wallet.address}: {data['error']}")
                                    if attempt == max_retries:
                                        return None
                                    await asyncio.sleep(2)
                                    continue
                                
                                result = data.get('data', {})
                                access_token = result.get('access_token')
                                aa_address = result.get('aa_address', aa_address)
                                displayed_name = result.get('displayed_name')
                                avatar_url = result.get('avatar_url')
                                cookie_header = extract_cookies(dict(retry_resp.headers))
                                
                                # Register new user
                                logger.info(f"Registering new user for {wallet.address}")
                                register_headers = BASE_HEADERS.copy()
                                register_headers['User-Agent'] = random.choice(USER_AGENTS)
                                register_headers['Authorization'] = f"Bearer {access_token}"
                                register_payload = {
                                    "registration_type_id": 1,
                                    "user_account_id": "",
                                    "user_account_name": "",
                                    "eoa_address": wallet.address.lower(),
                                    "smart_account_address": aa_address.lower(),
                                    "referral_code": "AMA1RZIB"
                                }
                                async with session.post(
                                    f"{GOKITE_URL}/auth",
                                    json=register_payload,
                                    headers=register_headers
                                ) as register_resp:
                                    if register_resp.status != 200:
                                        logger.error(f"User registration failed for {wallet.address}: HTTP {register_resp.status}")
                                        if attempt == max_retries:
                                            return None
                                        await asyncio.sleep(2)
                                        continue
                                    
                                    register_data = await register_resp.json()
                                    if register_data.get('error'):
                                        logger.error(f"User registration failed for {wallet.address}: {register_data['error']}")
                                        if attempt == max_retries:
                                            return None
                                        await asyncio.sleep(2)
                                        continue
                                    
                                    logger.info(f"User registration successful for {wallet.address}")
                                
                                # Proceed with profile fetch
                                if not aa_address:
                                    profile = await fetch_user_profile(access_token)
                                    aa_address = profile.get('profile', {}).get('smart_account_address') if profile else None
                                    if not aa_address:
                                        logger.error(f"No smart account address found for {wallet.address}")
                                        return None
                                
                                logger.info(f"Login successful for {wallet.address}")
                                return {
                                    'access_token': access_token,
                                    'aa_address': aa_address,
                                    'displayed_name': displayed_name,
                                    'avatar_url': avatar_url,
                                    'cookie_header': cookie_header
                                }
                        
                        elif resp.status != 200:
                            logger.error(f"Login failed for {wallet.address}: HTTP {resp.status}")
                            if attempt == max_retries:
                                return None
                            await asyncio.sleep(2)
                            continue
                        
                        data = await resp.json()
                        if data.get('error'):
                            logger.error(f"Login failed for {wallet.address}: {data['error']}")
                            if attempt == max_retries:
                                return None
                            await asyncio.sleep(2)
                            continue
                        
                        result = data.get('data', {})
                        access_token = result.get('access_token')
                        aa_address = result.get('aa_address')
                        displayed_name = result.get('displayed_name')
                        avatar_url = result.get('avatar_url')
                        cookie_header = extract_cookies(dict(resp.headers))
                        
                        if not aa_address:
                            profile = await fetch_user_profile(access_token)
                            aa_address = profile.get('profile', {}).get('smart_account_address') if profile else None
                            if not aa_address:
                                logger.error(f"No smart account address found for {wallet.address}")
                                return None
                        
                        logger.info(f"Login successful for {wallet.address}")
                        return {
                            'access_token': access_token,
                            'aa_address': aa_address,
                            'displayed_name': displayed_name,
                            'avatar_url': avatar_url,
                            'cookie_header': cookie_header
                        }
            except Exception as e:
                logger.error(f"Login error for {wallet.address}: {e}")
                if attempt == max_retries:
                    logger.error(f"Login failed for {wallet.address} after {max_retries} attempts. Check cookies or contact KiteAI support.")
                    return None
                await asyncio.sleep(2)
    return None

async def initialize_onboarding_quiz(access_token: str, eoa_address: str, cookie_header: Optional[str], max_retries: int = 3) -> bool:
    try:
        logger.info(f"Initializing onboarding quiz for EOA: {eoa_address}")
        url = f"{BASE_URL}/v2/quiz/onboard/get?eoa={eoa_address.lower()}"
        
        async with aiohttp.ClientSession() as session:
            for attempt in range(1, max_retries + 1):
                try:
                    headers = BASE_HEADERS.copy()
                    headers['User-Agent'] = random.choice(USER_AGENTS)
                    headers['Authorization'] = f"Bearer {access_token}"
                    headers['Accept-Language'] = 'en-US,en;q=0.9'
                    if cookie_header:
                        headers['Cookie'] = cookie_header
                    
                    logger.info(f"Attempting quiz initialization (attempt: {attempt})")
                    async with REQUEST_SEMAPHORE:
                        async with session.get(url, headers=headers) as resp:
                            response_text = await resp.text()
                            if resp.status != 200:
                                logger.error(f"Quiz initialization failed: HTTP {resp.status}, Response: {response_text}")
                                if attempt == max_retries:
                                    return False
                                await asyncio.sleep(2)
                                continue
                            
                            data = await resp.json()
                            if data.get('error'):
                                logger.error(f"Quiz initialization failed: {data['error']}")
                                if attempt == max_retries:
                                    return False
                                await asyncio.sleep(2)
                                continue
                            
                            logger.info(f"Quiz initialization successful for EOA: {eoa_address}")
                            return True
                except Exception as e:
                    logger.error(f"Error during quiz initialization (attempt {attempt}): {e}")
                    if attempt == max_retries:
                        return False
                    await asyncio.sleep(2)
        
        return False
    
    except Exception as e:
        logger.error(f"Failed to initialize onboarding quiz for EOA {eoa_address}: {e}")
        return False
    
async def complete_onboarding_quiz(access_token: str, eoa_address: str, cookie_header: Optional[str], max_retries: int = 3) -> bool:
    try:
        logger.info(f"Attempting to complete onboarding quiz for EOA: {eoa_address}")
        
        if not await initialize_onboarding_quiz(access_token, eoa_address, cookie_header, max_retries):
            logger.error(f"Cannot proceed with quiz submission due to initialization failure for EOA: {eoa_address}")
            return False
        
        url = f"{BASE_URL}/v2/quiz/onboard/submit"
        
        quiz_answers = [
            {"question_id": 1, "answer": "D", "finish": False},
            {"question_id": 2, "answer": "B", "finish": False},
            {"question_id": 3, "answer": "C", "finish": False},
            {"question_id": 4, "answer": "B", "finish": True},
        ]
        
        async with aiohttp.ClientSession() as session:
            for idx, answer in enumerate(quiz_answers, 1):
                for attempt in range(1, max_retries + 1):
                    try:
                        headers = BASE_HEADERS.copy()
                        headers['User-Agent'] = random.choice(USER_AGENTS)
                        headers['Authorization'] = f"Bearer {access_token}"
                        headers['Accept-Language'] = 'en-US,en;q=0.9' 
                        if cookie_header:
                            headers['Cookie'] = cookie_header
                        
                        payload = {
                            "question_id": answer["question_id"],
                            "answer": answer["answer"],
                            "finish": answer["finish"],
                            "eoa": eoa_address.lower()
                        }
                        logger.info(f"Submitting quiz answer {idx}/4 (question_id: {answer['question_id']}, attempt: {attempt})")
                        async with REQUEST_SEMAPHORE:
                            async with session.post(url, json=payload, headers=headers) as resp:
                                response_text = await resp.text()
                                if resp.status != 200:
                                    logger.error(f"Quiz submission failed for question {answer['question_id']}: HTTP {resp.status}, Response: {response_text}")
                                    if attempt == max_retries:
                                        return False
                                    await asyncio.sleep(1)
                                    continue
                                
                                data = await resp.json()
                                if data.get('error'):
                                    logger.error(f"Quiz submission failed for question {answer['question_id']}: {data['error']}")
                                    if attempt == max_retries:
                                        return False
                                    await asyncio.sleep(2)
                                    continue
                                
                                logger.info(f"Successfully submitted answer for question {answer['question_id']}")
                                break  # Move to next question
                    except Exception as e:
                        logger.error(f"Error submitting quiz answer {answer['question_id']} (attempt {attempt}): {e}")
                        if attempt == max_retries:
                            return False
                        await asyncio.sleep(2)
                
                await asyncio.sleep(1)
        
        logger.info(f"Onboarding quiz completed successfully for EOA: {eoa_address}")
        return True
    
    except Exception as e:
        logger.error(f"Failed to complete onboarding quiz for EOA {eoa_address}: {e}")
        return False

async def connect_x_account(access_token: str, action_type_ids: List[int], cookie_header: Optional[str] = None, max_retries: int = 5) -> bool:
    try:
        if not action_type_ids:
            logger.info("No social accounts to connect.")
            return True

        logger.info(f"Attempting to connect social accounts with action_type_ids: {action_type_ids}")
        url = f"{GOKITE_URL}/me/socials"
        all_success = True

        for action_type_id in action_type_ids:
            random_id = str(random.randint(10000000, 9999999999))
            try:
                fake = Faker()
                username = fake.user_name()
                username = re.sub(r"[^a-zA-Z0-9]", "", username)
                target_length = random.randint(9, 12)
                while len(username) < target_length:
                    username += str(random.randint(0, 9))
                username = username[:target_length]
            except Exception as e:
                logger.warning(f"Faker unavailable or failed: {e}. Using fallback username generation.")
                ADJECTIVES = ['Cool', 'Swift', 'Bright', 'Bold', 'Wise', 'Vivid', 'Calm', 'Neat']
                NOUNS = ['Star', 'Wave', 'Cloud', 'Peak', 'Tree', 'Stone', 'Moon', 'River']
                adjective = random.choice(ADJECTIVES)
                noun = random.choice(NOUNS)
                number = str(random.randint(0, 9999))
                username = f"{adjective}{noun}{number}"
                target_length = random.randint(12, 17)
                while len(username) < target_length:
                    username += str(random.randint(0, 9))
                username = username[:target_length]

            logger.info(f"Generated social account details: id={random_id}, username={username}, action_type_id={action_type_id}")

            payload = {
                "action_type_id": action_type_id,
                "id": random_id,
                "username": username
            }

            async with aiohttp.ClientSession() as session:
                for attempt in range(1, max_retries + 1):
                    try:
                        headers = BASE_HEADERS.copy()
                        headers.update({
                            'Authorization': f"Bearer {access_token}",
                            'User-Agent': random.choice(USER_AGENTS),
                            'sec-ch-ua': '"Microsoft Edge";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
                            'sec-ch-ua-mobile': '?0',
                            'sec-ch-ua-platform': '"Windows"',
                            'Accept': 'application/json, text/plain, */*',
                            'Content-Type': 'application/json',
                            'Origin': 'https://testnet.gokite.ai',
                            'Referer': 'https://testnet.gokite.ai/',
                            'Accept-Language': 'en-US,en;q=0.9'
                        })
                        if cookie_header:
                            headers['Cookie'] = cookie_header

                        logger.info(f"Submitting social account (action_type_id={action_type_id}, attempt: {attempt})")
                        async with REQUEST_SEMAPHORE:
                            async with session.post(url, json=payload, headers=headers) as resp:
                                response_text = await resp.text()
                                if resp.status != 200:
                                    logger.error(f"Social account connection failed: HTTP {resp.status}, Response: {response_text}")
                                    if attempt == max_retries:
                                        all_success = False
                                    await asyncio.sleep(2)
                                    continue

                                data = await resp.json()
                                if data.get('error'):
                                    logger.error(f"Social account connection failed: {data['error']}, Response: {response_text}")
                                    if attempt == max_retries:
                                        all_success = False
                                    await asyncio.sleep(2)
                                    continue

                                logger.info(f"Social account connected successfully: id={random_id}, username={username}, action_type_id={action_type_id}")
                                break
                    except Exception as e:
                        logger.error(f"Error during social account connection (action_type_id={action_type_id}, attempt {attempt}): {e}")
                        if attempt == max_retries:
                            all_success = False
                        await asyncio.sleep(2)

        return all_success

    except Exception as e:
        logger.error(f"Failed to connect social account: {e}")
        return False
        
async def fetch_user_profile(access_token: str) -> Optional[Dict[str, Any]]:
    try:
        headers = BASE_HEADERS.copy()
        headers['Authorization'] = f"Bearer {access_token}"
        headers['User-Agent'] = random.choice(USER_AGENTS)
        async with REQUEST_SEMAPHORE, aiohttp.ClientSession() as session:
            async with session.get(f"{GOKITE_URL}/me", headers=headers) as resp:
                if resp.status != 200:
                    logger.error(f"Failed to fetch profile: HTTP {resp.status}")
                    return None
                data = await resp.json()
                if data.get('error'):
                    logger.error(f"Failed to fetch profile: {data['error']}")
                    return None
                return data.get('data')
    except Exception as e:
        logger.error(f"Profile fetch error: {e}")
        return None

async def claim_daily_faucet(access_token: str, cookie_header: Optional[str]) -> bool:
    try:
        logger.info("Attempting to claim daily faucet")
        
        headers = BASE_HEADERS.copy()
        headers.update({
            'Authorization': f"Bearer {access_token}",
            'X-Recaptcha-Token': 'null', 
            'User-Agent': random.choice(USER_AGENTS),
            'sec-ch-ua': '"Microsoft Edge";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/json',
            'Referer': 'https://testnet.gokite.ai/'
        })
        if cookie_header:
            headers['Cookie'] = cookie_header
        
        payload = {} 
        
        async with REQUEST_SEMAPHORE, aiohttp.ClientSession() as session:
            async with session.post(
                f"{GOKITE_URL}/blockchain/faucet-transfer",
                json=payload,
                headers=headers
            ) as resp:
                response_text = await resp.text()
                if resp.status != 200:
                    logger.error(f"Faucet claim failed: HTTP {resp.status}, Response: {response_text}")
                    return False
                data = await resp.json()
                if data.get('error'):
                    logger.error(f"Faucet claim failed: {data['error']}, Response: {response_text}")
                    return False
                logger.info("Daily faucet claimed successfully")
                return True
    except Exception as e:
        logger.error(f"Faucet claim error: {e}")
        return False

async def create_daily_quiz(access_token: str, eoa_address: str, cookie_header: Optional[str], max_retries: int = 3) -> Optional[int]:
    try:
        logger.info(f"Creating daily quiz for EOA: {eoa_address}")
        url = f"{BASE_URL}/v2/quiz/create"
        current_date = datetime.now().strftime("%Y-%m-%d")
        payload = {
            "title": f"daily_quiz_{current_date}",
            "num": 1,
            "eoa": eoa_address.lower()
        }

        async with aiohttp.ClientSession() as session:
            for attempt in range(1, max_retries + 1):
                try:
                    headers = BASE_HEADERS.copy()
                    headers.update({
                        'Authorization': f"Bearer {access_token}",
                        'User-Agent': random.choice(USER_AGENTS),
                        'Accept': 'application/json, text/plain, */*',
                        'Accept-Language': 'en-US,en;q=0.9',
                        'Content-Type': 'application/json',
                        'Origin': 'https://testnet.gokite.ai',
                        'Referer': 'https://testnet.gokite.ai/',
                        'Cache-Control': 'no-cache',
                        'Pragma': 'no-cache',
                        'Priority': 'u=1, i',
                        'sec-ch-ua': '"Microsoft Edge";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
                        'sec-ch-ua-mobile': '?0',
                        'sec-ch-ua-platform': '"Windows"'
                    })
                    if cookie_header:
                        headers['Cookie'] = cookie_header

                    logger.info(f"Attempting quiz creation (attempt: {attempt})")
                    async with REQUEST_SEMAPHORE:
                        async with session.post(url, json=payload, headers=headers) as resp:
                            response_text = await resp.text()
                            if resp.status != 200:
                                logger.error(f"Quiz creation failed: HTTP {resp.status}, Response: {response_text}")
                                if attempt == max_retries:
                                    return None
                                await asyncio.sleep(2)
                                continue

                            data = await resp.json()
                            if data.get('error'):
                                logger.error(f"Quiz creation failed: {data['error']}")
                                if attempt == max_retries:
                                    return None
                                await asyncio.sleep(2)
                                continue

                            quiz_id = data.get('data', {}).get('quiz_id')
                            if not quiz_id:
                                logger.error("No quiz_id in response")
                                if attempt == max_retries:
                                    return None
                                await asyncio.sleep(2)
                                continue

                            logger.info(f"Daily quiz created successfully, quiz_id: {quiz_id}")
                            return quiz_id
                except Exception as e:
                    logger.error(f"Error during quiz creation (attempt {attempt}): {e}")
                    if attempt == max_retries:
                        return None
                    await asyncio.sleep(2)
        return None
    except Exception as e:
        logger.error(f"Failed to create daily quiz for EOA {eoa_address}: {e}")
        return None

async def load_daily_quiz(access_token: str, eoa_address: str, quiz_id: int, cookie_header: Optional[str], max_retries: int = 3) -> Optional[Dict[str, Any]]:
    try:
        logger.info(f"Loading daily quiz for EOA: {eoa_address}, quiz_id: {quiz_id}")
        url = f"{BASE_URL}/v2/quiz/get?id={quiz_id}&eoa={eoa_address.lower()}"

        async with aiohttp.ClientSession() as session:
            for attempt in range(1, max_retries + 1):
                try:
                    headers = BASE_HEADERS.copy()
                    headers.update({
                        'Authorization': f"Bearer {access_token}",
                        'User-Agent': random.choice(USER_AGENTS),
                        'Accept': 'application/json, text/plain, */*',
                        'Accept-Language': 'en-US,en;q=0.9',
                        'Origin': 'https://testnet.gokite.ai',
                        'Referer': 'https://testnet.gokite.ai/',
                        'Cache-Control': 'no-cache',
                        'Pragma': 'no-cache',
                        'Priority': 'u=1, i',
                        'sec-ch-ua': '"Microsoft Edge";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
                        'sec-ch-ua-mobile': '?0',
                        'sec-ch-ua-platform': '"Windows"'
                    })
                    if cookie_header:
                        headers['Cookie'] = cookie_header

                    logger.info(f"Attempting quiz load (attempt: {attempt})")
                    async with REQUEST_SEMAPHORE:
                        async with session.get(url, headers=headers) as resp:
                            response_text = await resp.text()
                            if resp.status != 200:
                                logger.error(f"Quiz load failed: HTTP {resp.status}, Response: {response_text}")
                                if attempt == max_retries:
                                    return None
                                await asyncio.sleep(2)
                                continue

                            data = await resp.json()
                            if data.get('error'):
                                logger.error(f"Quiz load failed: {data['error']}")
                                if attempt == max_retries:
                                    return None
                                await asyncio.sleep(2)
                                continue

                            quiz_data = data.get('data')
                            if not quiz_data or not quiz_data.get('question'):
                                logger.error("No question data in quiz response")
                                if attempt == max_retries:
                                    return None
                                await asyncio.sleep(2)
                                continue

                            logger.info(f"Daily quiz loaded successfully, quiz_id: {quiz_id}")
                            return quiz_data
                except Exception as e:
                    logger.error(f"Error during quiz load (attempt {attempt}): {e}")
                    if attempt == max_retries:
                        return None
                    await asyncio.sleep(2)
        return None
    except Exception as e:
        logger.error(f"Failed to load daily quiz for EOA {eoa_address}: {e}")
        return None

async def submit_daily_quiz(access_token: str, eoa_address: str, quiz_id: int, question_id: int, answer: str, cookie_header: Optional[str], max_retries: int = 3) -> bool:
    try:
        logger.info(f"Submitting daily quiz for EOA: {eoa_address}, quiz_id: {quiz_id}, question_id: {question_id}")
        url = f"{BASE_URL}/v2/quiz/submit"
        payload = {
            "quiz_id": quiz_id,
            "question_id": question_id,
            "answer": answer,
            "finish": True,
            "eoa": eoa_address.lower()
        }

        async with aiohttp.ClientSession() as session:
            for attempt in range(1, max_retries + 1):
                try:
                    headers = BASE_HEADERS.copy()
                    headers.update({
                        'Authorization': f"Bearer {access_token}",
                        'User-Agent': random.choice(USER_AGENTS),
                        'Accept': 'application/json, text/plain, */*',
                        'Accept-Language': 'en-US,en;q=0.9',
                        'Content-Type': 'application/json',
                        'Origin': 'https://testnet.gokite.ai',
                        'Referer': 'https://testnet.gokite.ai/',
                        'Cache-Control': 'no-cache',
                        'Pragma': 'no-cache',
                        'Priority': 'u=1, i',
                        'sec-ch-ua': '"Microsoft Edge";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
                        'sec-ch-ua-mobile': '?0',
                        'sec-ch-ua-platform': '"Windows"'
                    })
                    if cookie_header:
                        headers['Cookie'] = cookie_header

                    logger.info(f"Attempting quiz submission (attempt: {attempt})")
                    async with REQUEST_SEMAPHORE:
                        async with session.post(url, json=payload, headers=headers) as resp:
                            response_text = await resp.text()
                            if resp.status != 200:
                                logger.error(f"Quiz submission failed: HTTP {resp.status}, Response: {response_text}")
                                if attempt == max_retries:
                                    return False
                                await asyncio.sleep(2)
                                continue

                            data = await resp.json()
                            if data.get('error'):
                                logger.error(f"Quiz submission failed: {data['error']}")
                                if attempt == max_retries:
                                    return False
                                await asyncio.sleep(2)
                                continue

                            logger.info(f"Daily quiz submitted successfully, quiz_id: {quiz_id}")
                            return True
                except Exception as e:
                    logger.error(f"Error during quiz submission (attempt {attempt}): {e}")
                    if attempt == max_retries:
                        return False
                    await asyncio.sleep(2)
        return False
    except Exception as e:
        logger.error(f"Failed to submit daily quiz for EOA {eoa_address}: {e}")
        return False

async def fetch_available_badges(access_token: str, max_retries: int = 3) -> Optional[List[Dict[str, Any]]]:
    try:
        logger.info("Fetching available badges")
        url = f"{GOKITE_URL}/badges"

        async with aiohttp.ClientSession() as session:
            for attempt in range(1, max_retries + 1):
                try:
                    headers = BASE_HEADERS.copy()
                    headers.update({
                        'Authorization': f"Bearer {access_token}",
                        'User-Agent': random.choice(USER_AGENTS),
                        'Accept': 'application/json, text/plain, */*',
                        'Accept-Language': 'en-US,en;q=0.9',
                        'Origin': 'https://testnet.gokite.ai',
                        'Referer': 'https://testnet.gokite.ai/',
                        'Cache-Control': 'no-cache',
                        'Pragma': 'no-cache',
                        'Priority': 'u=1, i',
                        'sec-ch-ua': '"Microsoft Edge";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
                        'sec-ch-ua-mobile': '?0',
                        'sec-ch-ua-platform': '"Windows"'
                    })

                    logger.info(f"Attempting badge fetch (attempt: {attempt})")
                    async with REQUEST_SEMAPHORE:
                        async with session.get(url, headers=headers) as resp:
                            response_text = await resp.text()
                            if resp.status != 200:
                                logger.error(f"Badge fetch failed: HTTP {resp.status}, Response: {response_text}")
                                if attempt == max_retries:
                                    return None
                                await asyncio.sleep(2)
                                continue

                            data = await resp.json()
                            if data.get('error'):
                                logger.error(f"Badge fetch failed: {data['error']}")
                                if attempt == max_retries:
                                    return None
                                await asyncio.sleep(2)
                                continue

                            badges = data.get('data', [])
                            logger.info(f"Fetched {len(badges)} available badges")
                            return badges
                except Exception as e:
                    logger.error(f"Error during badge fetch (attempt {attempt}): {e}")
                    if attempt == max_retries:
                        return None
                    await asyncio.sleep(2)
        return None
    except Exception as e:
        logger.error(f"Failed to fetch badges: {e}")
        return None

async def mint_badge(access_token: str, badge_id: int, max_retries: int = 3) -> bool:
    try:
        logger.info(f"Attempting to mint badge, badge_id: {badge_id}")
        url = f"{GOKITE_URL}/badges/mint"
        payload = {"badge_id": badge_id}

        async with aiohttp.ClientSession() as session:
            for attempt in range(1, max_retries + 1):
                try:
                    headers = BASE_HEADERS.copy()
                    headers.update({
                        'Authorization': f"Bearer {access_token}",
                        'User-Agent': random.choice(USER_AGENTS),
                        'Accept': 'application/json, text/plain, */*',
                        'Accept-Language': 'en-US,en;q=0.9',
                        'Content-Type': 'application/json',
                        'Origin': 'https://testnet.gokite.ai',
                        'Referer': 'https://testnet.gokite.ai/',
                        'Cache-Control': 'no-cache',
                        'Pragma': 'no-cache',
                        'Priority': 'u=1, i',
                        'sec-ch-ua': '"Microsoft Edge";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
                        'sec-ch-ua-mobile': '?0',
                        'sec-ch-ua-platform': '"Windows"'
                    })

                    logger.info(f"Attempting badge mint (attempt: {attempt})")
                    async with REQUEST_SEMAPHORE:
                        async with session.post(url, json=payload, headers=headers) as resp:
                            response_text = await resp.text()
                            if resp.status != 200:
                                logger.error(f"Badge mint failed: HTTP {resp.status}, Response: {response_text}")
                                if attempt == max_retries:
                                    return False
                                await asyncio.sleep(2)
                                continue

                            data = await resp.json()
                            if data.get('error'):
                                logger.error(f"Badge mint failed: {data['error']}")
                                if attempt == max_retries:
                                    return False
                                await asyncio.sleep(2)
                                continue

                            logger.info(f"Badge minted successfully, badge_id: {badge_id}")
                            return True
                except Exception as e:
                    logger.error(f"Error during badge mint (attempt {attempt}): {e}")
                    if attempt == max_retries:
                        return False
                    await asyncio.sleep(2)
        return False
    except Exception as e:
        logger.error(f"Failed to mint badge {badge_id}: {e}")
        return False
        
async def fetch_stake_info(access_token: str, cookie_header: Optional[str]) -> Optional[Dict[str, Any]]:
    try:
        logger.info("Fetching stake information")
        headers = BASE_HEADERS.copy()
        headers['Authorization'] = f"Bearer {access_token}"
        headers['User-Agent'] = random.choice(USER_AGENTS)
        if cookie_header:
            headers['Cookie'] = cookie_header
        
        async with REQUEST_SEMAPHORE, aiohttp.ClientSession() as session:
            async with session.get(f"{GOKITE_URL}/subnet/3/staked-info?id=3", headers=headers) as resp:
                if resp.status != 200:
                    logger.error(f"Failed to fetch stake info: HTTP {resp.status}")
                    return None
                data = await resp.json()
                if data.get('error'):
                    logger.error(f"Failed to fetch stake info: {data['error']}")
                    return None
                return data.get('data')
    except Exception as e:
        logger.error(f"Stake info fetch error: {e}")
        return None
    
async def fetch_balance(access_token: str, cookie_header: Optional[str] = None, max_retries: int = 3) -> Optional[float]:
    try:
        logger.info("Fetching wallet balance")
        url = f"{GOKITE_URL}/me/balance"
        
        async with aiohttp.ClientSession() as session:
            for attempt in range(1, max_retries + 1):
                try:
                    headers = BASE_HEADERS.copy()
                    headers.update({
                        'Authorization': f"Bearer {access_token}",
                        'User-Agent': random.choice(USER_AGENTS),
                        'Accept': 'application/json, text/plain, */*',
                        'Accept-Language': 'en-US,en;q=0.9',
                        'Origin': 'https://testnet.gokite.ai',
                        'Referer': 'https://testnet.gokite.ai/',
                        'Cache-Control': 'no-cache',
                        'Pragma': 'no-cache',
                        'Priority': 'u=1, i',
                        'sec-ch-ua': '"Microsoft Edge";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
                        'sec-ch-ua-mobile': '?0',
                        'sec-ch-ua-platform': '"Windows"'
                    })
                    if cookie_header:
                        headers['Cookie'] = cookie_header

                    logger.info(f"Attempting balance fetch (attempt: {attempt})")
                    async with REQUEST_SEMAPHORE:
                        async with session.get(url, headers=headers) as resp:
                            response_text = await resp.text()
                            if resp.status != 200:
                                logger.error(f"Balance fetch failed: HTTP {resp.status}, Response: {response_text}")
                                if attempt == max_retries:
                                    return None
                                await asyncio.sleep(2)
                                continue

                            data = await resp.json()
                            if data.get('error'):
                                logger.error(f"Balance fetch failed: {data['error']}")
                                if attempt == max_retries:
                                    return None
                                await asyncio.sleep(2)
                                continue

                            kite_balance = data.get('data', {}).get('balances', {}).get('kite', 0)
                            logger.info(f"Fetched KITE balance: {kite_balance}")
                            return float(kite_balance)
                except Exception as e:
                    logger.error(f"Error during balance fetch (attempt {attempt}): {e}")
                    if attempt == max_retries:
                        return None
                    await asyncio.sleep(2)
        return None
    except Exception as e:
        logger.error(f"Failed to fetch balance: {e}")
        return None
    
async def stake_token(access_token: str, cookie_header: Optional[str], max_retries: int = 2) -> bool:
    try:
        stake_amount = STAKING_AMOUNT
        logger.info(f"Attempting to stake {stake_amount} KITE token")
        headers = BASE_HEADERS.copy()
        headers['Authorization'] = f"Bearer {access_token}"
        headers['User-Agent'] = random.choice(USER_AGENTS)
        if cookie_header:
            headers['Cookie'] = cookie_header
        
        payload = {'subnet_address': KITE_AI_SUBNET, 'amount': stake_amount}
        async with REQUEST_SEMAPHORE:
            for attempt in range(1, max_retries + 1):
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(f"{GOKITE_URL}/subnet/delegate", json=payload, headers=headers) as resp:
                            if resp.status != 200:
                                logger.error(f"Stake failed: HTTP {resp.status}")
                                if attempt == max_retries:
                                    return False
                                await asyncio.sleep(5)
                                continue
                            data = await resp.json()
                            if data.get('error'):
                                logger.error(f"Stake failed: {data['error']}")
                                if attempt == max_retries:
                                    return False
                                await asyncio.sleep(5)
                                continue
                            logger.info(f"Successfully staked {stake_amount} KITE token")
                            return True
                except Exception as e:
                    logger.error(f"Stake attempt {attempt} error: {e}")
                    if attempt == max_retries:
                        return False
                    await asyncio.sleep(5)
        return False
    except Exception as e:
        logger.error(f"Stake error: {e}")
        return False

async def claim_stake_rewards(access_token: str, cookie_header: Optional[str], max_retries: int = 2) -> bool:
    try:
        logger.info("Attempting to claim stake rewards")
        headers = BASE_HEADERS.copy()
        headers['Authorization'] = f"Bearer {access_token}"
        headers['User-Agent'] = random.choice(USER_AGENTS)
        if cookie_header:
            headers['Cookie'] = cookie_header
        
        payload = {'subnet_address': KITE_AI_SUBNET}
        async with REQUEST_SEMAPHORE:
            for attempt in range(1, max_retries + 1):
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(f"{GOKITE_URL}/subnet/claim-rewards", json=payload, headers=headers) as resp:
                            if resp.status != 200:
                                logger.error(f"Claim rewards failed: HTTP {resp.status}")
                                if attempt == max_retries:
                                    return False
                                await asyncio.sleep(5)
                                continue
                            data = await resp.json()
                            if data.get('error'):
                                logger.error(f"Claim rewards failed: {data['error']}")
                                if attempt == max_retries:
                                    return False
                                await asyncio.sleep(5)
                                continue
                            reward = data.get('data', {}).get('claim_amount', 0)
                            logger.info(f"Successfully claimed {reward} KITE rewards")
                            return True
                except Exception as e:
                    logger.error(f"Claim rewards attempt {attempt} error: {e}")
                    if attempt == max_retries:
                        return False
                    await asyncio.sleep(5)
        return False
    except Exception as e:
        logger.error(f"Claim rewards error: {e}")
        return False

async def interact_with_agent(session: aiohttp.ClientSession,access_token: str,aa_address: str,cookie_header: Optional[str],agent: Dict[str, str],prompt: str,interaction_count: int) -> Optional[Dict[str, Any]]:
    try:
        if not aa_address:
            logger.error(f"Cannot interact with {agent['name']}: No smart account address")
            return None

        logger.info(f"Interaction {interaction_count} with {agent['name']} - Prompt: {prompt}")

        headers = BASE_HEADERS.copy()
        headers['Authorization'] = f"Bearer {access_token}"
        headers['Accept'] = 'text/event-stream'
        headers['User-Agent'] = random.choice(USER_AGENTS)
        if cookie_header:
            headers['Cookie'] = cookie_header

        payload = {
            'service_id': agent['service_id'],
            'subnet': 'kite_ai_labs',
            'stream': True,
            'body': {'stream': True, 'message': prompt}
        }

        async with REQUEST_SEMAPHORE:
            async with session.post(f"{GOKITE_URL}/agent/inference", json=payload, headers=headers) as resp:
                if resp.status != 200:
                    logger.error(f"Inference failed for {agent['name']}: HTTP {resp.status}")
                    return None

                output = ""
                async for line in resp.content:
                    line = line.decode('utf-8').strip()
                    if line.startswith('data: ') and line != 'data: [DONE]':
                        try:
                            data = json.loads(line[6:])
                            content = data.get('choices', [{}])[0].get('delta', {}).get('content', '')
                            if content:
                                output += content
                                if len(output) > 100:
                                    output = output[:100] + '...'
                                    break
                        except json.JSONDecodeError:
                            continue

        receipt_headers = BASE_HEADERS.copy()
        receipt_headers['Authorization'] = f"Bearer {access_token}"
        receipt_headers['User-Agent'] = random.choice(USER_AGENTS)
        if cookie_header:
            receipt_headers['Cookie'] = cookie_header

        receipt_payload = {
            'address': aa_address,
            'service_id': agent['service_id'],
            'input': [{'type': 'text/plain', 'value': prompt}],
            'output': [{'type': 'text/plain', 'value': output or 'No response'}]
        }

        async with session.post(f"{BASE_URL}/v2/submit_receipt", json=receipt_payload, headers=receipt_headers) as receipt_resp:
            if receipt_resp.status != 200:
                logger.error(f"Receipt submission failed for {agent['name']}: HTTP {receipt_resp.status}")
                return None

            receipt_data = await receipt_resp.json()
            if receipt_data.get('error'):
                logger.error(f"Receipt submission failed for {agent['name']}: {receipt_data['error']}")
                return None

            receipt_id = receipt_data.get('data', {}).get('id')
            logger.info(f"Interaction {interaction_count} - Receipt submitted, ID: {receipt_id}")
            return {'receipt_id': receipt_id, 'agent': agent['name']}

    except Exception as e:
        logger.error(f"Error interacting with {agent['name']}: {e}")
        return None

def get_next_run_time() -> datetime:
    now = datetime.now()
    next_run = now + timedelta(hours=3)
    next_run = next_run.replace(minute=0, second=0, microsecond=0)
    return next_run

async def display_countdown(next_run_time: datetime, interaction_count: int):
    while True:
        now = datetime.now()
        time_left = next_run_time - now
        if time_left.total_seconds() <= 0:
            logger.info("Starting new daily run")
            await daily_task(interaction_count)
            return
        hours, remainder = divmod(time_left.total_seconds(), 3600)
        minutes, seconds = divmod(remainder, 60)
        logger.info(f"Next run in: {int(hours)}h {int(minutes)}m {int(seconds)}s")
        await asyncio.sleep(1)

async def process_wallet(wallet_info: Dict[str, Optional[str]], prompts: Dict[str, List[str]], interaction_count: int) -> None:
    try:
        wallet = create_wallet(wallet_info['private_key'])
        if not wallet:
            logger.error(f"Skipping invalid wallet: {wallet_info['private_key'][:10]}...")
            return
        
        async with WALLET_SEMAPHORE:
            logger.info(f"Processing wallet: {wallet.address}")
            
            auth_data = await authenticate_wallet(
                wallet,
                wallet_info['neo_session'],
                wallet_info['refresh_token']
            )
            if not auth_data:
                logger.error(f"Authentication failed for wallet: {wallet.address}")
                return
            
            access_token = auth_data['access_token']
            aa_address = auth_data['aa_address']
            displayed_name = auth_data['displayed_name']
            cookie_header = auth_data['cookie_header']
            
            profile = await fetch_user_profile(access_token)
            if profile:
                profile_data = profile.get('profile', {})
                logger.info(f"User: {profile_data.get('displayed_name', displayed_name or 'Unknown')}")
                logger.info(f"EOA Address: {profile_data.get('eoa_address', wallet.address)}")
                logger.info(f"Smart Account: {profile_data.get('smart_account_address', aa_address)}")
                logger.info(f"Total XP Points: {profile_data.get('total_xp_points', 0)}")
                logger.info(f"Referral Code: {profile_data.get('referral_code', 'None')}")
                badges_minted = profile_data.get('badges_minted') or []
                logger.info(f"Badges Minted: {len(badges_minted)}")
                logger.info(f"Twitter Connected: {'Yes' if profile_data.get('social_accounts', {}).get('twitter', {}).get('id') else 'No'}")
                logger.info(f"Discord Connected: {'Yes' if profile_data.get('social_accounts', {}).get('discord', {}).get('id') else 'No'}")
                
                # Complete onboarding quiz if not done
                if not profile.get('onboarding_quiz_completed', False):
                    logger.info(f"Onboarding quiz not completed for {wallet.address}. Attempting to complete.")
                    success = await complete_onboarding_quiz(
                        access_token=access_token,
                        eoa_address=wallet.address,
                        cookie_header=cookie_header
                    )
                    if not success:
                        logger.error(f"Failed to complete onboarding quiz for {wallet.address}. Continuing with other tasks.")
                    else:
                        profile = await fetch_user_profile(access_token)
                        if profile and profile.get('onboarding_quiz_completed'):
                            logger.info(f"Onboarding quiz status updated to completed for {wallet.address}")
                
                # Complete daily quiz if not done
                if not profile.get('daily_quiz_completed', False):
                    logger.info(f"Daily quiz not completed for {wallet.address}. Attempting to complete.")
                    quiz_id = await create_daily_quiz(
                        access_token=access_token,
                        eoa_address=wallet.address,
                        cookie_header=cookie_header
                    )
                    if quiz_id:
                        quiz_data = await load_daily_quiz(
                            access_token=access_token,
                            eoa_address=wallet.address,
                            quiz_id=quiz_id,
                            cookie_header=cookie_header
                        )
                        if quiz_data and quiz_data.get('question'):
                            question = quiz_data['question'][0]
                            question_id = question.get('question_id')
                            answer = question.get('answer')
                            if question_id and answer:
                                success = await submit_daily_quiz(
                                    access_token=access_token,
                                    eoa_address=wallet.address,
                                    quiz_id=quiz_id,
                                    question_id=question_id,
                                    answer=answer,
                                    cookie_header=cookie_header
                                )
                                if success:
                                    logger.info(f"Daily quiz completed successfully for {wallet.address}")
                                    profile = await fetch_user_profile(access_token)
                                    if profile and profile.get('daily_quiz_completed'):
                                        logger.info(f"Daily quiz status updated to completed for {wallet.address}")
                                else:
                                    logger.error(f"Failed to submit daily quiz for {wallet.address}")
                            else:
                                logger.error(f"Invalid quiz question data for {wallet.address}")
                        else:
                            logger.error(f"Failed to load daily quiz for {wallet.address}")
                    else:
                        logger.error(f"Failed to create daily quiz for {wallet.address}")
            
            # Fetch stake info
            stake_info = await fetch_stake_info(access_token, cookie_header)
            if stake_info:
                logger.info("----- Stake Information -----")
                logger.info(f"My Staked Amount: {stake_info.get('my_staked_amount')} tokens")
                logger.info(f"Total Staked Amount: {stake_info.get('staked_amount')} tokens")
                logger.info(f"Delegator Count: {stake_info.get('delegator_count')}")
                logger.info(f"APR: {stake_info.get('apr')}%")
                logger.info("-----------------------------")
            
            # Claim faucet only if claimable
            if profile and profile.get('faucet_claimable', False):
                faucet_claimed = await claim_daily_faucet(access_token, cookie_header)
                if faucet_claimed:
                    logger.info("Waiting 5 seconds for faucet tokens to arrive...")
                    await asyncio.sleep(5)
            else:
                logger.info(f"Faucet not claimable for {wallet.address}. Skipping faucet claim.")
            
            # Check balance before staking
            kite_balance = await fetch_balance(access_token, cookie_header)
            if kite_balance is not None:
                logger.info(f"KITE balance for {wallet.address}: {kite_balance}")
                if kite_balance >= STAKING_AMOUNT:
                    logger.info(f"Sufficient balance ({kite_balance} >= {STAKING_AMOUNT}). Proceeding to stake.")
                    await stake_token(access_token, cookie_header)
                else:
                    logger.info(f"Insufficient balance ({kite_balance} < {STAKING_AMOUNT}). Skipping staking.")
            else:
                logger.error(f"Failed to fetch balance for {wallet.address}. Skipping staking.")
            
            # Claim stake rewards
            await claim_stake_rewards(access_token, cookie_header)

            # Connect X and Discord accounts if not already connected
            action_type_ids = []
            social_accounts = profile.get('social_accounts', {}) if profile else {}
            twitter = social_accounts.get('twitter', {})
            discord = social_accounts.get('discord', {})
            
            if not (twitter.get('id') and twitter.get('username')):
                action_type_ids.append(3)  # Twitter/X
            if not (discord.get('id') and discord.get('username')):
                action_type_ids.append(16)  # Discord
            
            if action_type_ids:
                logger.info(f"Connecting social accounts for action_type_ids: {action_type_ids}")
                success = await connect_x_account(access_token, action_type_ids, cookie_header)
                if success:
                    logger.info("Social account connection successful")
                    profile = await fetch_user_profile(access_token)
                else:
                    logger.error("Failed to connect social accounts")
            else:
                logger.info("Twitter and Discord already connected. Skipping connection.")
            
            # Mint eligible badges
            if profile:
                badges_minted = profile.get('profile', {}).get('badges_minted') or []
                minted_badge_ids = {badge['id'] for badge in badges_minted}
                badges = await fetch_available_badges(access_token)
                if badges:
                    for badge in badges:
                        badge_id = badge.get('collectionId')
                        if badge.get('isEligible', False) and badge_id not in minted_badge_ids:
                            badge_name = badge.get('name', 'Unknown')
                            logger.info(f"Attempting to mint eligible badge: {badge_name} (ID: {badge_id})")
                            success = await mint_badge(access_token, badge_id)
                            if success:
                                logger.info(f"Successfully minted badge: {badge_name} (ID: {badge_id})")
                                profile = await fetch_user_profile(access_token)
                                minted_badge_ids.add(badge_id)  # Update local set
                            else:
                                logger.error(f"Failed to mint badge: {badge_name} (ID: {badge_id})")
                        else:
                            if badge_id in minted_badge_ids:
                                logger.info(f"Badge {badge.get('name', 'Unknown')} (ID: {badge_id}) already minted. Skipping.")
            
            # Interact with agents
            async with aiohttp.ClientSession() as session:
                agent_tasks = [
                    interact_with_agent(session, access_token, aa_address, cookie_header, agent, get_random_prompt(agent['name'], prompts), i + 1)
                    for agent in AGENTS
                    for i in range(interaction_count)
                ]
                results = await asyncio.gather(*agent_tasks, return_exceptions=True)

            
            logger.info(f"Completed processing wallet: {wallet.address}")
    
    except Exception as e:
        logger.error(f"Error processing wallet {wallet_info['private_key'][:10]}...: {e}")

async def daily_task(interaction_count: int):
    try:
        logger.info("=== KiteAI Auto Bot - Airdrop Insiders ===")
        prompts = await load_prompts()
        wallets = await load_private_keys()
        
        wallet_tasks = [
            process_wallet(wallet_info, prompts, interaction_count)
            for wallet_info in wallets
        ]
        await asyncio.gather(*wallet_tasks, return_exceptions=True)
        
        logger.info("Script execution completed")
        next_run_time = get_next_run_time()
        logger.info(f"Next run scheduled at: {next_run_time}")
        await display_countdown(next_run_time, interaction_count)
    except Exception as e:
        logger.error(f"Script error: {e}")
        next_run_time = get_next_run_time()
        logger.info(f"Next run scheduled at: {next_run_time}")
        await display_countdown(next_run_time, interaction_count)

async def main():
    try:
        interaction_count = None
        while interaction_count is None:
            try:
                count = NUM_OF_AI_INTERACTIONS
                interaction_count = int(count)
                if interaction_count < 1 or interaction_count > 99999:
                    logger.error("Invalid. Please enter a number between 1 and 99999.")
                    interaction_count = None
            except ValueError:
                logger.error("Invalid. Please enter a valid number.")
        
        
        await daily_task(interaction_count)
    except Exception as e:
        logger.error(f"Script initialization error: {e}")
        next_run_time = get_next_run_time()
        logger.info(f"Next run scheduled at: {next_run_time}")
        await display_countdown(next_run_time, interaction_count)

if __name__ == "__main__":
    asyncio.run(main())