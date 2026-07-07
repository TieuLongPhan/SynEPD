(function() {
    function injectHomeLink() {
        const navbarIconLinks = document.querySelectorAll(".navbar-icon-links");
        if (navbarIconLinks.length === 0) return false;
        
        let injected = false;
        navbarIconLinks.forEach(function(container) {
            if (!container.querySelector('a[title="Web Explorer"]')) {
                const li = document.createElement("li");
                li.className = "nav-item";
                li.innerHTML = `
                    <a href="https://synepd.bioinf.uni-leipzig.de" title="Web Explorer" class="nav-link pst-navbar-icon" data-bs-toggle="tooltip" data-bs-placement="bottom">
                        <i class="fa-solid fa-house fa-lg" aria-hidden="true"></i>
                        <span class="sr-only">Web Explorer</span>
                    </a>
                `;
                container.insertBefore(li, container.firstChild);
                injected = true;
            }
        });
        return injected;
    }

    // Try immediately
    if (!injectHomeLink()) {
        // Try on DOMContentLoaded
        document.addEventListener("DOMContentLoaded", injectHomeLink);
        // Try with interval as fallback
        let attempts = 0;
        const interval = setInterval(function() {
            attempts++;
            if (injectHomeLink() || attempts > 20) {
                clearInterval(interval);
            }
        }, 100);
    }
})();
