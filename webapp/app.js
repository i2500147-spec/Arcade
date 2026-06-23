const tg = window.Telegram.WebApp;
tg.expand();
tg.ready();

let userId = 0, userBalance = 0, refCode = '', refEarned = 0;
let crashActive = false, crashMultiplier = 1, crashBet = 0, crashInterval = null;
let slotMult = 1, currentSlot = 'novice';
let upgradeFrom = null, upgradeTo = null;
let openingCase = false, currentCase = '';

const API = 'https://arcade-8ru7.onrender.com/api';

if (tg.initDataUnsafe?.user) {
    userId = tg.initDataUnsafe.user.id;
    document.getElementById('avatar').src = tg.initDataUnsafe.user.photo_url || '';
    loadUserData();
}

async function loadUserData() {
    try {
        const r = await fetch(`${API}/user/${userId}`);
        const d = await r.json();
        userBalance = d.balance || 0;
        refCode = d.ref_code || '';
        refEarned = d.ref_earned || 0;
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
    let el = document.getElementById('page-' + page);
    if (el) el.classList.add('active');
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    let map = {ref:0,home:1,leaderboard:2,inventory:3,shop:4};
    if (map[page] !== undefined) document.querySelectorAll('.nav-btn')[map[page]].classList.add('active');
    if (page === 'leaderboard') loadLeaderboard();
    if (page === 'inventory') loadInventory();
    if (page === 'shop') loadShop();
    if (page === 'ref' && !refCode) tg.sendData(JSON.stringify({action:'get_ref'}));
}

function copyRef() {
    navigator.clipboard.writeText(`https://t.me/Casinoarcadebot?start=ref_${refCode}`).then(() => tg.showAlert('Скопировано!'));
}

function openDeposit() { document.getElementById('page-deposit').classList.add('active'); }
function closeDeposit() { document.getElementById('page-deposit').classList.remove('active'); }

function deposit() {
    let v = parseInt(document.getElementById('dep-amount').value);
    if (!v || v < 1) { tg.showAlert('Введите сумму!'); return; }
    tg.sendData(JSON.stringify({action:'pay',amount:v}));
    tg.showAlert('Счёт открыт!');
    closeDeposit();
}

function usePromo() {
    let c = document.getElementById('promo-code').value.trim().toUpperCase();
    if (!c) { tg.showAlert('Введите промокод!'); return; }
    tg.sendData(JSON.stringify({action:'promo',code:c}));
    document.getElementById('promo-code').value = '';
}

// КРАШ
function openCrash() { document.getElementById('page-crash').classList.add('active'); }
function startCrash() {
    if (crashActive) return;
    let bet = parseInt(document.getElementById('crash-bet').value);
    if (!bet || bet < 1) { tg.showAlert('Введите ставку!'); return; }
    if (bet > userBalance) { tg.showAlert('Недостаточно звёзд!'); return; }
    crashBet = bet; crashMultiplier = 1; crashActive = true;
    userBalance -= bet; updateUI();
    document.getElementById('crash-cashout').disabled = false;
    document.getElementById('crash-mult').style.color = '#27ae60';
    document.getElementById('crash-graph').innerHTML = '';
    crashInterval = setInterval(() => {
        crashMultiplier += 0.05;
        document.getElementById('crash-mult').textContent = crashMultiplier.toFixed(2) + 'x';
        let bar = document.createElement('div');
        bar.style.cssText = `width:4px;height:${Math.min(crashMultiplier*15,100)}px;background:#27ae60;display:inline-block;margin:1px;vertical-align:bottom`;
        document.getElementById('crash-graph').appendChild(bar);
        if (document.getElementById('crash-graph').children.length > 80) document.getElementById('crash-graph').removeChild(document.getElementById('crash-graph').firstChild);
        if (Math.random() < 0.02 * crashMultiplier) endCrash(false);
    }, 200);
}
function cashout() { if (crashActive) endCrash(true); }
function endCrash(won) {
    clearInterval(crashInterval); crashActive = false;
    document.getElementById('crash-cashout').disabled = true;
    if (won) {
        let w = Math.floor(crashBet * crashMultiplier);
        userBalance += w;
        document.getElementById('crash-mult').textContent = 'Выигрыш: ' + w + ' ⭐';
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

// СЛОТЫ
function openSlots() {
    document.getElementById('page-slots').classList.add('active');
    buildSlotReels();
    updateSlotInfo();
}
function buildSlotReels() {
    let c = slotMult * 3, h = '';
    for (let i = 0; i < c; i++) h += '<div class="slot-reel">⭐</div>';
    document.getElementById('slot-reels').innerHTML = h;
}
function switchSlot(type) {
    currentSlot = type;
    document.querySelectorAll('.slot-tab').forEach(t => t.classList.remove('active'));
    event.target.classList.add('active');
    buildSlotReels();
    updateSlotInfo();
    let colors = {novice:'#2d7dd2',start:'#c0392b',major:'#d4a843'};
    document.getElementById('slot-reels').style.borderColor = colors[type];
}
function updateSlotInfo() {
    let info = {novice:'Призы: 1⭐, 3⭐, 5⭐, 10⭐, 305⭐',start:'Призы: 1-25⭐, 🔥305⭐, 💎500⭐',major:'Призы: 🔥305⭐, 💎800⭐'};
    document.getElementById('slot-info').textContent = info[currentSlot];
}
function setSlotMult(m) { slotMult = m; document.querySelectorAll('.mult-btn').forEach(b => b.classList.remove('active')); event.target.classList.add('active'); buildSlotReels(); }
function spinSlots() {
    let prices = {novice:3,start:10,major:305};
    let price = prices[currentSlot] * slotMult;
    if (price > userBalance) { tg.showAlert('Недостаточно звёзд!'); return; }
    userBalance -= price; updateUI();
    let items = {novice:['⭐','💫','💎','🎁','💰'],start:['⭐','💫','💎','🔥','💰','👑','💍','🎉'],major:['🔥','💎']};
    let reels = document.querySelectorAll('.slot-reel');
    let count = 0;
    let spin = setInterval(() => {
        reels.forEach(r => r.textContent = items[currentSlot][Math.floor(Math.random()*items[currentSlot].length)]);
        count++;
        if (count > 30) {
            clearInterval(spin);
            let vals = {novice:[1,3,5,10,305],start:[1,2,3,5,10,25,305,500],major:[305,800]};
            let total = 0;
            reels.forEach(() => total += vals[currentSlot][Math.floor(Math.random()*vals[currentSlot].length)]);
            userBalance += total;
            updateUI();
            tg.showAlert('Выигрыш: ' + total + '⭐!');
        }
    }, 80);
}

// КЕЙСЫ
function showCaseDetail(name) {
    currentCase = name;
    let cases = {
        valera:{icon:'👤',name:'Валера',price:3,items:['⭐ 3','⭐ 5','⭐ 10']},
        bumzhikha:{icon:'👩',name:'Бомжиха',price:5,items:['⭐ 5','⭐ 15','⭐ 30','⭐ 50']},
        svidanie:{icon:'💝',name:'Свидание',price:50,items:['⭐ 5','⭐ 4','⭐ 3','⭐ 7','⭐ 50','⭐ 75','⭐ 100','⭐ 200']},
        otel:{icon:'🏨',name:'Отель',price:75,items:['⭐ 5','⭐ 4','⭐ 3','⭐ 1','⭐ 80','⭐ 150','⭐ 200']},
        forever:{icon:'💎',name:'Forever',price:300,items:['⭐ 1','⭐ 10','⭐ 4','⭐ 350','⭐ 400','⭐ 500','⭐ 1000']},
        allornothing:{icon:'🎰',name:'Всё или ничего',price:2000,items:['⭐ 1000','⭐ 5000']}
    };
    let c = cases[name];
    document.getElementById('case-detail-content').innerHTML = `
        <div style="text-align:center;font-size:60px;margin-bottom:12px;">${c.icon}</div>
        <div style="text-align:center;font-size:22px;font-weight:bold;color:var(--gold);">${c.name}</div>
        <div style="text-align:center;color:var(--gray);margin-bottom:16px;">${c.price}⭐</div>
        <div style="display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin-bottom:20px;">
            ${c.items.map(i => `<div style="background:var(--border);padding:10px 14px;border-radius:10px;font-size:14px;">${i}</div>`).join('')}
        </div>
        <button class="btn-gold big" onclick="openCase('${name}')">Открыть</button>
    `;
    goPage('case-detail');
}

function openFreeCase() { if (!openingCase) { openingCase = true; startCaseAnim(); tg.sendData(JSON.stringify({action:'open_case',case:'daily'})); } }
function openCase(name) {
    if (openingCase) return;
    let prices = {valera:3,bumzhikha:5,svidanie:50,otel:75,forever:300,allornothing:2000};
    if (prices[name] && prices[name] > userBalance) { tg.showAlert('Недостаточно звёзд!'); return; }
    openingCase = true;
    startCaseAnim();
    tg.sendData(JSON.stringify({action:'open_case',case:name}));
}

function startCaseAnim() {
    document.getElementById('page-case-open').classList.add('active');
    document.getElementById('case-result-block').style.display = 'none';
    document.getElementById('case-reels').innerHTML = '';
    for (let i = 0; i < 5; i++) {
        let reel = document.createElement('div');
        reel.className = 'case-reel-item';
        reel.textContent = ['📦','💎','⭐','💫','🔥','💰','👑'][Math.floor(Math.random()*7)];
        document.getElementById('case-reels').appendChild(reel);
    }
}

function closeCaseOpen() {
    if (openingCase) return;
    document.getElementById('page-case-open').classList.remove('active');
    goPage('home');
}

// ИНВЕНТАРЬ
async function loadInventory() {
    let r = await fetch(`${API}/inventory/${userId}`);
    let items = await r.json();
    let list = document.getElementById('inventory-list');
    if (!items.length) { list.innerHTML = '<p class="empty">Здесь пусто</p>'; return; }
    list.innerHTML = items.map(i => `
        <div class="inv-item">
            <div class="nft-icon nft-${i.icon||'default'}"></div>
            <span class="inv-name">${i.name}</span>
            <span class="inv-value">${i.value}⭐</span>
            <button class="inv-sell" onclick="sellNFT('${i.name}',${i.value})">Продать</button>
        </div>`).join('');
}
async function sellNFT(name, value) {
    let r = await fetch(`${API}/sell_nft`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({uid:userId,name,value})});
    let d = await r.json();
    if (d.success) { userBalance = d.balance; updateUI(); tg.showAlert('Продано!'); loadInventory(); }
}

// МАГАЗИН
async function loadShop() {
    let r = await fetch(`${API}/shop`);
    let items = await r.json();
    document.getElementById('shop-list').innerHTML = items.map(i => `
        <div class="shop-item">
            <div class="nft-icon nft-${i.icon}"></div>
            <span class="shop-name">${i.name}</span>
            <span class="shop-price">${i.value}⭐</span>
            <button class="shop-buy" onclick="buyNFT('${i.name}',${i.value},'${i.icon}')">Купить</button>
        </div>`).join('');
}
async function buyNFT(name, value, icon) {
    let r = await fetch(`${API}/buy_nft`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({uid:userId,name,value,icon})});
    let d = await r.json();
    if (d.success) { userBalance = d.balance; updateUI(); tg.showAlert('Куплено!'); loadInventory(); }
    else tg.showAlert('Недостаточно звёзд!');
}

// АПГРЕЙД
function openUpgrade() { document.getElementById('page-upgrade').classList.add('active'); }
function closeUpgrade() { document.getElementById('page-upgrade').classList.remove('active'); }
async function selectFromNFT() {
    let r = await fetch(`${API}/inventory/${userId}`), items = await r.json();
    if (items.length) { upgradeFrom = items[0]; document.getElementById('from-nft').textContent = items[0].name; updateWheel(); }
}
async function selectToNFT() {
    let r = await fetch(`${API}/shop`), items = await r.json();
    if (items.length) { upgradeTo = items[0]; document.getElementById('to-nft').textContent = items[0].name; updateWheel(); }
}
function updateWheel() {
    if (upgradeFrom && upgradeTo) {
        let chance = Math.max(1, Math.min(50, Math.floor(upgradeFrom.value / upgradeTo.value * 100)));
        document.getElementById('upgrade-chance').textContent = chance + '%';
        document.querySelector('.wheel').style.setProperty('--p', chance + '%');
    }
}
async function doUpgrade() {
    if (!upgradeFrom || !upgradeTo) { tg.showAlert('Выберите NFT!'); return; }
    let r = await fetch(`${API}/upgrade`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({uid:userId,from:upgradeFrom,to:upgradeTo})});
    let d = await r.json();
    tg.showAlert(d.won ? '🎉 Успех!' : '❌ Сгорел!');
    closeUpgrade();
}

// ЛИДЕРБОРД
async function loadLeaderboard() {
    let r = await fetch(`${API}/leaderboard`), d = await r.json();
    let list = document.getElementById('leaderboard-list');
    list.innerHTML = d.top.map((t,i) => `<div class="lb-row"><span class="lb-place">#${i+1}</span><div class="lb-avatar">${(t.name||'U')[0]}</div><span class="lb-name">${t.name||'User'}</span><span class="lb-amount">${t.total}⭐</span></div>`).join('');
}

function openDuel() { tg.showAlert('Дуэли скоро!'); }

// ОБРАБОТКА ОТВЕТОВ ОТ БОТА
let origPostMessage = window.postMessage;
window.postMessage = function(msg, origin) {
    if (typeof msg === 'string' && msg.startsWith('case:')) {
        let parts = msg.split(':');
        if (parts[1] === 'error') {
            openingCase = false;
            document.getElementById('page-case-open').classList.remove('active');
            let err = parts[2];
            if (err === 'not_subscribed') tg.showAlert('Подпишитесь на @arcade_ludo!');
            else if (err === 'already_opened') tg.showAlert('Уже открывали!');
            else if (err === 'no_balance') tg.showAlert('Недостаточно звёзд!');
            else tg.showAlert('Ошибка!');
        } else if (parts[1] === 'success') {
            openingCase = false;
            userBalance = parseInt(parts[4]); updateUI();
            document.getElementById('case-result-block').style.display = 'block';
            document.getElementById('case-result-text').innerHTML = `<b>${parts[2]}</b><br>+${parts[3]}⭐`;
            setTimeout(() => {
                document.getElementById('page-case-open').classList.remove('active');
                document.getElementById('case-result-block').style.display = 'none';
                goPage('home');
            }, 3000);
        }
    }
    if (typeof msg === 'string' && msg.startsWith('withdraw:')) {
        let s = msg.split(':')[1];
        if (s === 'ok') tg.showAlert('✅ Заявка создана!');
        else if (s === 'min') tg.showAlert('❌ Минимум 100⭐');
        else if (s === 'no_balance') tg.showAlert('❌ Недостаточно звёзд!');
    }
    if (typeof msg === 'string' && msg.startsWith('ref:')) refCode = msg.split(':')[1];
    if (typeof msg === 'string' && msg.startsWith('promo:')) {
        let s = msg.split(':')[1];
        if (s === 'success') { userBalance += parseInt(msg.split(':')[2]); updateUI(); tg.showAlert('+'+msg.split(':')[2]+'⭐!'); }
        else tg.showAlert('Промокод недействителен!');
    }
    origPostMessage.call(this, msg, origin);
};
