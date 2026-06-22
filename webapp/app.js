const tg = window.Telegram.WebApp;
tg.expand(); 
tg.ready();

let userId = 0;
let userBalance = 0;
let refCode = '';
let refEarned = 0;
let crashActive = false;
let crashMultiplier = 1;
let crashBet = 0;
let crashInterval = null;
let slotMult = 1;
let currentSlot = 'novice';
let upgradeFrom = null;
let upgradeTo = null;
let openingCase = false;

const API = 'https://arcade-8ru7.onrender.com/api';

const initData = tg.initDataUnsafe;
if (initData.user) {
    userId = initData.user.id;
    document.getElementById('avatar').src = initData.user.photo_url || '';
    loadUserData();
}

async function loadUserData() {
    try {
        const res = await fetch(`${API}/user/${userId}`);
        const data = await res.json();
        userBalance = data.balance || 0;
        refCode = data.ref_code || '';
        refEarned = data.ref_earned || 0;
        updateUI();
    } catch(e) {}
}

function updateUI() {
    document.getElementById('balance').textContent = userBalance;
    document.getElementById('ref-earned').textContent = refEarned;
}

function goPage(page) {
    if (crashActive || openingCase) return;
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    const el = document.getElementById('page-' + page);
    if (el) el.classList.add('active');
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    const navMap = { ref: 0, home: 1, leaderboard: 2, inventory: 3, shop: 4 };
    if (navMap[page] !== undefined) document.querySelectorAll('.nav-btn')[navMap[page]].classList.add('active');
    if (page === 'leaderboard') loadLeaderboard();
    if (page === 'inventory') loadInventory();
    if (page === 'shop') loadShop();
    if (page === 'ref') loadRefCode();
}

async function loadRefCode() {
    if (refCode) return;
    tg.sendData(JSON.stringify({ action: 'get_ref' }));
}

function copyRef() {
    const link = `https://t.me/arcadecasinobot?start=ref_${refCode}`;
    navigator.clipboard.writeText(link).then(() => tg.showAlert('Скопировано!'));
}

function openDeposit() { document.getElementById('page-deposit').classList.add('active'); }
function closeDeposit() { document.getElementById('page-deposit').classList.remove('active'); }
function connectWallet() { document.getElementById('wallet-status').textContent = '✅ Подключен'; tg.showAlert('Готово!'); }

function deposit() {
    const amount = parseInt(document.getElementById('dep-amount').value);
    if (!amount || amount < 1) { tg.showAlert('Введите сумму!'); return; }
    tg.sendData(JSON.stringify({ action: 'pay', amount: amount }));
    tg.showAlert('Счёт открыт!');
    closeDeposit();
}

function usePromo() {
    const code = document.getElementById('promo-code').value.trim().toUpperCase();
    if (!code) { tg.showAlert('Введите промокод!'); return; }
    tg.sendData(JSON.stringify({ action: 'promo', code: code }));
    document.getElementById('promo-code').value = '';
}

function openCrash() { document.getElementById('page-crash').classList.add('active'); }
function openSlots() {
    document.getElementById('page-slots').classList.add('active');
    updateSlotLoot();
}
function openUpgrade() { document.getElementById('page-upgrade').classList.add('active'); }
function closeUpgrade() { document.getElementById('page-upgrade').classList.remove('active'); }

function startCrash() {
    if (crashActive) return;
    const bet = parseInt(document.getElementById('crash-bet').value);
    if (!bet || bet < 1) { tg.showAlert('Введите ставку!'); return; }
    if (bet > userBalance) { tg.showAlert('Недостаточно звёзд!'); return; }
    crashBet = bet; crashMultiplier = 1; crashActive = true;
    userBalance -= bet; updateUI();
    document.getElementById('crash-cashout').disabled = false;
    document.getElementById('crash-mult').style.color = '#27ae60';
    crashInterval = setInterval(() => {
        crashMultiplier += 0.05;
        document.getElementById('crash-mult').textContent = crashMultiplier.toFixed(2) + 'x';
        if (Math.random() < 0.02 * crashMultiplier) endCrash(false);
    }, 200);
}

function cashout() { if (crashActive) endCrash(true); }

function endCrash(won) {
    clearInterval(crashInterval); crashActive = false;
    document.getElementById('crash-cashout').disabled = true;
    if (won) {
        const win = Math.floor(crashBet * crashMultiplier);
        userBalance += win;
        document.getElementById('crash-mult').textContent = 'Выигрыш: ' + win + ' ⭐';
        document.getElementById('crash-mult').style.color = '#27ae60';
    } else {
        document.getElementById('crash-mult').textContent = 'Краш!';
        document.getElementById('crash-mult').style.color = '#c0392b';
    }
    updateUI();
}

function closeGame() {
    if (crashActive) { tg.showAlert('Дождитесь конца!'); return; }
    document.getElementById('page-crash').classList.remove('active');
    document.getElementById('page-slots').classList.remove('active');
    goPage('home');
}

function switchSlot(type) {
    currentSlot = type;
    document.querySelectorAll('.slot-tab').forEach(t => t.classList.remove('active'));
    event.target.classList.add('active');
    updateSlotLoot();
    const colors = { novice: '#2d7dd2', start: '#c0392b', major: '#d4a843' };
    document.getElementById('slot-reels').style.borderColor = colors[type];
}

function updateSlotLoot() {
    const loots = {
        novice: [['⭐ 1 звезда',''], ['⭐ 3 звезды',''], ['⭐ 5 звёзд',''], ['⭐ 10 звёзд',''], ['⭐ 305 звёзд','']],
        start: [['⭐ 1',''], ['⭐ 2',''], ['⭐ 3',''], ['⭐ 5',''], ['⭐ 10',''], ['⭐ 25',''], ['🔥 Нфт 305⭐',''], ['💎 Нфт 500⭐','']],
        major: [['🔥 Нфт 305⭐',''], ['💎 Нфт 800⭐','']]
    };
    document.getElementById('slot-loot').innerHTML = loots[currentSlot].map(l => `<div class="loot-row"><span>${l[0]}</span></div>`).join('');
}

function setSlotMult(m) {
    slotMult = m;
    document.querySelectorAll('.mult-btn').forEach(b => b.classList.remove('active'));
    event.target.classList.add('active');
}

function spinSlots() {
    const prices = { novice: 3, start: 10, major: 305 };
    const price = prices[currentSlot] * slotMult;
    if (price > userBalance) { tg.showAlert('Недостаточно звёзд!'); return; }
    userBalance -= price; updateUI();
    const items = { novice: ['⭐','⭐','⭐','⭐','💫'], start: ['⭐','💫','💎','🔥','💰'], major: ['🔥','💎'] };
    const reels = [document.getElementById('reel1'), document.getElementById('reel2'), document.getElementById('reel3')];
    let count = 0;
    const spin = setInterval(() => {
        reels.forEach(r => r.textContent = items[currentSlot][Math.floor(Math.random() * items[currentSlot].length)]);
        count++;
        if (count > 30) {
            clearInterval(spin);
            const vals = { novice: [1,3,5,10,305], start: [1,2,3,5,10,25,305,500], major: [305,800] };
            const result = vals[currentSlot][Math.floor(Math.random() * vals[currentSlot].length)];
            userBalance += result * slotMult;
            updateUI();
            tg.showAlert(`Выигрыш: ${result}⭐ x${slotMult}!`);
        }
    }, 80);
}

function openFreeCase() { if (!openingCase) { openingCase = true; animateCase(); tg.sendData(JSON.stringify({ action: 'open_case', case: 'daily' })); } }
function openCase(name) { if (!openingCase) { openingCase = true; animateCase(); tg.sendData(JSON.stringify({ action: 'open_case', case: name })); } }

function animateCase() {
    document.getElementById('page-case-open').classList.add('active');
    document.getElementById('case-result-block').style.display = 'none';
    const reel = document.getElementById('case-reel');
    const icons = ['📦','💎','⭐','💫','🔥','💰','👑','💍'];
    let count = 0;
    const spin = setInterval(() => {
        reel.textContent = icons[Math.floor(Math.random() * icons.length)];
        count++;
        if (count > 25) clearInterval(spin);
    }, 80);
}

function closeCaseOpen() {
    if (openingCase) return;
    document.getElementById('page-case-open').classList.remove('active');
    document.getElementById('case-result-block').style.display = 'none';
    goPage('home');
}

async function loadInventory() {
    const res = await fetch(`${API}/inventory/${userId}`);
    const items = await res.json();
    const list = document.getElementById('inventory-list');
    if (!items.length) { list.innerHTML = '<p class="empty">Здесь пусто</p>'; return; }
    list.innerHTML = items.map(i => `
        <div class="inv-item">
            <div class="nft-icon nft-${i.icon || 'default'}"></div>
            <span class="inv-name">${i.name}</span>
            <span class="inv-value">${i.value}⭐</span>
            <button class="inv-sell" onclick="sellNFT('${i.name}',${i.value})">Продать</button>
        </div>
    `).join('');
}

async function sellNFT(name, value) {
    const res = await fetch(`${API}/sell_nft`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ uid: userId, name, value }) });
    const data = await res.json();
    if (data.success) { userBalance = data.balance; updateUI(); tg.showAlert('Продано!'); loadInventory(); }
}

async function loadShop() {
    const res = await fetch(`${API}/shop`);
    const items = await res.json();
    document.getElementById('shop-list').innerHTML = items.map(i => `
        <div class="shop-item">
            <div class="nft-icon nft-${i.icon}"></div>
            <span class="shop-name">${i.name}</span>
            <span class="shop-price">${i.value}⭐</span>
            <button class="shop-buy" onclick="buyNFT('${i.name}',${i.value},'${i.icon}')">Купить</button>
        </div>
    `).join('');
}

async function buyNFT(name, value, icon) {
    const res = await fetch(`${API}/buy_nft`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ uid: userId, name, value, icon }) });
    const data = await res.json();
    if (data.success) { userBalance = data.balance; updateUI(); tg.showAlert('Куплено!'); } else tg.showAlert('Недостаточно звёзд!');
}

async function selectFromNFT() {
    const res = await fetch(`${API}/inventory/${userId}`); const items = await res.json();
    if (items.length) { upgradeFrom = items[0]; document.getElementById('from-nft').textContent = items[0].name; updateUpgradeChance(); }
}

async function selectToNFT() {
    const res = await fetch(`${API}/shop`); const items = await res.json();
    if (items.length) { upgradeTo = items[0]; document.getElementById('to-nft').textContent = items[0].name; updateUpgradeChance(); }
}

function updateUpgradeChance() {
    if (upgradeFrom && upgradeTo) document.getElementById('upgrade-chance').textContent = Math.max(1, Math.min(50, Math.floor(upgradeFrom.value / upgradeTo.value * 100))) + '%';
}

async function doUpgrade() {
    if (!upgradeFrom || !upgradeTo) { tg.showAlert('Выберите NFT!'); return; }
    const res = await fetch(`${API}/upgrade`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ uid: userId, from: upgradeFrom, to: upgradeTo }) });
    const data = await res.json();
    tg.showAlert(data.won ? '🎉 Успех!' : '❌ Сгорел!');
    closeUpgrade();
}

async function loadLeaderboard() {
    const res = await fetch(`${API}/leaderboard`);
    const data = await res.json();
    const list = document.getElementById('leaderboard-list');
    list.innerHTML = data.top.map((t, i) => `<div class="lb-row"><span class="lb-place">#${i+1}</span><div class="lb-avatar">${(t.name||'U')[0]}</div><span class="lb-name">${t.name||'User'}</span><span class="lb-amount">${t.total}⭐</span></div>`).join('');
    list.innerHTML += `<p class="sub-text" style="margin-top:12px;">Всего выведено: ${data.total_withdrawn}⭐</p>`;
}

function openDuel() { tg.showAlert('Дуэли скоро!'); }

const origPostMessage = window.postMessage;
window.postMessage = function(msg, origin) {
    if (typeof msg === 'string' && msg.startsWith('case:')) {
        const parts = msg.split(':');
        if (parts[1] === 'error') {
            openingCase = false;
            document.getElementById('page-case-open').classList.remove('active');
            const err = parts[2];
            if (err === 'not_subscribed') tg.showAlert('Подпишитесь на @arcade_ludo!');
            else if (err === 'already_opened') tg.showAlert('Уже открывали!');
            else if (err === 'no_balance') tg.showAlert('Недостаточно звёзд!');
            else tg.showAlert('Ошибка!');
        } else if (parts[1] === 'success') {
            openingCase = false;
            userBalance = parseInt(parts[4]); updateUI();
            document.getElementById('case-result-block').style.display = 'block';
            document.getElementById('case-result-text').innerHTML = `<b>${parts[2]}</b><br>+${parts[3]}⭐`;
        }
    }
    if (typeof msg === 'string' && msg.startsWith('withdraw:')) {
        const s = msg.split(':')[1];
        if (s === 'ok') tg.showAlert('✅ Заявка создана!');
        else if (s === 'min') tg.showAlert('❌ Минимум 100⭐');
        else if (s === 'no_balance') tg.showAlert('❌ Недостаточно звёзд!');
    }
    if (typeof msg === 'string' && msg.startsWith('ref:')) {
        refCode = msg.split(':')[1];
    }
    if (typeof msg === 'string' && msg.startsWith('promo:')) {
        const s = msg.split(':')[1];
        if (s === 'success') {
            const stars = parseInt(msg.split(':')[2]);
            userBalance += stars;
            updateUI();
            tg.showAlert(`+${stars}⭐!`);
        } else {
            tg.showAlert('Промокод недействителен!');
        }
    }
    origPostMessage.call(this, msg, origin);
};
